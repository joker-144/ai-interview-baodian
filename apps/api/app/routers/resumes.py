"""引擎 A 输入侧：简历上传 → 解析 → 能力维度评估。

真实链路（一期）：
multipart 上传 → 本地抽取纯文本（PDF/DOCX/TXT，见 app/resume_parser.py）
→ 主模型结构化与能力维度评估（app/analyzer.py）→ 保存最近一份解析结果，
同时留存原文与画像摘要供出题提示词使用（不对外回显）。

扫描件/图片型 PDF 暂无 OCR（多模态分层已在管理端预留），会返回可读提示。

出题任务不在本路由：统一走 `POST /api/question-sets/generate`（见 question_sets.py），
三通道（简历 / 岗位检索 / JD 定向）共用同一入口，由 body.source 区分。
"""

from fastapi import APIRouter, File, HTTPException, UploadFile

from app import analyzer, llm, resume_parser, store
from app.schemas import ResumeAnalysis

router = APIRouter(prefix="/api/resumes", tags=["resumes"])

# 一期 Mock 只保存最近一份解析结果；二期落 resumes 表后改为按 id 查询
LATEST_RESUME_ID = "resume-latest"
MAX_FILE_MB = 10


@router.post("", response_model=ResumeAnalysis)
async def upload_resume(file: UploadFile = File(...)) -> dict:
    """上传简历并解析（真实解析 + 主模型结构化，耗时约 10~30s）。"""
    file_name = file.filename or "resume"
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="上传文件为空")
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"文件超过 {MAX_FILE_MB}MB 上限")

    try:
        text = resume_parser.extract_text(file_name, data)
    except resume_parser.UnsupportedFormat as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        analysis, summary = await analyzer.analyze_resume(text, file_name)
    except llm.LlmError as exc:
        raise HTTPException(status_code=502, detail=f"简历解析失败：{exc}") from exc

    store.state.resume_analysis = analysis
    store.state.resume_text = text
    store.state.resume_summary = summary
    return analysis


@router.get("/latest", response_model=ResumeAnalysis | None)
def get_latest_analysis() -> dict | None:
    """读取最近一次简历解析结果（含能力维度评估与预估题量）。"""
    return store.state.resume_analysis


@router.get("/{resume_id}", response_model=ResumeAnalysis)
def get_analysis(resume_id: str) -> dict:
    """按 id 读取解析结果：一期仅 `resume-latest` 有效，二期落库后支持多份简历。"""
    if resume_id != LATEST_RESUME_ID or store.state.resume_analysis is None:
        raise HTTPException(status_code=404, detail="简历不存在或尚未解析")
    return store.state.resume_analysis
