"""引擎 A 输入侧：简历上传 → 解析 → 能力维度评估。

真实链路（一期）：
multipart 上传 → 本地抽取纯文本（PDF/DOCX/TXT，见 app/resume_parser.py）
→ 主模型结构化与能力维度评估（app/analyzer.py）→ 保存最近一份解析结果，
同时留存原文与画像摘要供出题提示词使用（不对外回显）。

扫描件/图片型 PDF 本地抽不出文字时，自动转 vision 分层多模态 OCR 兜底
（app/resume_ocr.py）；OCR 不可用（分层停用 / 模型不支持）时返回可读提示。

按 JWT 用户隔离：每个用户只保存自己最近一份解析产物（resumes 表一行）。

出题任务不在本路由：统一走 `POST /api/question-sets/generate`（见 question_sets.py），
三通道（简历 / 岗位检索 / JD 定向）共用同一入口，由 body.source 区分。
"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app import analyzer, llm, resume_ocr, resume_parser, store
from app.auth import get_current_user
from app.schemas import ResumeAnalysis

router = APIRouter(prefix="/api/resumes", tags=["resumes"])

# 一期只保存最近一份解析结果；二期落库后按 id 查询多份简历
LATEST_RESUME_ID = "resume-latest"
MAX_FILE_MB = 10


@router.post("", response_model=ResumeAnalysis)
async def upload_resume(
    file: UploadFile = File(...), user_id: str = Depends(get_current_user)
) -> dict:
    """上传简历并解析（真实解析 + 主模型结构化，耗时约 10~30s）。

    扫描件/图片型 PDF 自动转多模态 OCR 再解析，耗时会更长（页图渲染 + 视觉模型转写）。
    """
    file_name = file.filename or "resume"
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="上传文件为空")
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"文件超过 {MAX_FILE_MB}MB 上限")

    try:
        text = resume_parser.extract_text(file_name, data)
    except resume_parser.ScannedDocument as exc:
        # 扫描件/图片型 PDF：转 vision 分层 OCR；不可用时给可读降级提示
        try:
            text = await resume_ocr.ocr_pdf(data)
        except resume_ocr.OcrError as inner:
            raise HTTPException(status_code=422, detail=str(inner)) from inner
        except llm.LlmError as inner:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"扫描件 OCR 失败：{inner}；"
                    "请在管理端检查「多模态模型」分层配置，或上传可复制文字的版本"
                ),
            ) from inner
    except resume_parser.UnsupportedFormat as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        analysis, summary = await analyzer.analyze_resume(text, file_name)
    except llm.LlmError as exc:
        raise HTTPException(status_code=502, detail=f"简历解析失败：{exc}") from exc

    store.state.resumes[user_id] = {"analysis": analysis, "text": text, "summary": summary}
    store.persist_resume(user_id)
    return analysis


@router.get("/latest", response_model=ResumeAnalysis | None)
def get_latest_analysis(user_id: str = Depends(get_current_user)) -> dict | None:
    """读取最近一次简历解析结果（含能力维度评估与预估题量）。"""
    return (store.user_resume(user_id) or {}).get("analysis")


@router.get("/{resume_id}", response_model=ResumeAnalysis)
def get_analysis(resume_id: str, user_id: str = Depends(get_current_user)) -> dict:
    """按 id 读取解析结果：一期仅 `resume-latest` 有效，二期落库后支持多份简历。"""
    if resume_id != LATEST_RESUME_ID or not store.user_resume(user_id):
        raise HTTPException(status_code=404, detail="简历不存在或尚未解析")
    return (store.user_resume(user_id) or {})["analysis"]
