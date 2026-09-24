"""引擎 A 输入侧：简历上传 → 解析 → 能力维度评估。

一期 Mock：解析结果为种子数据（文件名透传）。二期接 LangGraph 流水线
（文档解析 → LLM 结构化 → 能力评估 → Planner），仅需替换 _mock 解析实现。

出题任务不在本路由：统一走 `POST /api/question-sets/generate`（见 question_sets.py），
三通道（简历 / 岗位检索 / JD 定向）共用同一入口，由 body.source 区分。
"""

from fastapi import APIRouter, HTTPException

from app import store
from app.schemas import ResumeAnalysis, ResumeUploadRequest

router = APIRouter(prefix="/api/resumes", tags=["resumes"])

# 一期 Mock 只保存最近一份解析结果；二期落 resumes 表后改为按 id 查询
LATEST_RESUME_ID = "resume-latest"


@router.post("", response_model=ResumeAnalysis)
def upload_resume(body: ResumeUploadRequest) -> dict:
    """上传并解析简历（Mock：直接返回种子解析结果，文件名透传）。"""
    analysis = dict(store.RESUME_ANALYSIS_SEED)
    analysis["fileName"] = body.fileName
    store.state.resume_analysis = analysis
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
