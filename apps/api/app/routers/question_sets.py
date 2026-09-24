"""题集与出题任务（文档 4.5：统一在 `/api/question-sets` 命名空间）。

一期真实链路：
- 题集 = 内存种子 + 用户自建 + 引擎生成（删除时级联清理刷题进度与错题）；
- 出题任务由**独立后台协程**推进（app/generation.py 真调 LLM），SSE 端点只做观察者
  ——客户端断开不影响生成，轮询兜底接口可继续拿到进度；
- 引擎 B（岗位检索）/ 引擎 C（JD 定向）二期接入时，仅需在 start_generate 内
  按 `source` 分派到不同流水线，接口契约不变。
"""

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app import generation, store
from app.schemas import (
    GenerateRequest,
    GenerateTaskOut,
    Question,
    QuestionSet,
    SetCreateRequest,
)

USER_ID = store.USER_ID

# 题量三档口径（文档 6.x：精简 40 / 标准 80 / 深度 120，默认 80）
COUNT_LIMITS = {"lite": 40, "standard": 80, "deep": 120}
DEFAULT_COUNT = 80
MAX_COUNT = max(COUNT_LIMITS.values())

router = APIRouter(prefix="/api/question-sets", tags=["question_sets"])


# ---------------- 题集 CRUD ----------------


@router.get("", response_model=list[QuestionSet])
def list_sets() -> list[dict]:
    return store.state.sets


@router.post("", response_model=QuestionSet, status_code=201)
def create_set(body: SetCreateRequest) -> dict:
    """新建自定义题集（初始为空，题目由引擎生成或手动加入）。"""
    new_set = {
        "id": store.new_id("set"),
        "source": body.source,
        "title": body.title,
        "questionCount": 0,
        "updatedAt": store.now_str(),
    }
    store.add_set(new_set)
    return new_set


# ---------------- 出题任务（引擎 A/B/C 统一入口） ----------------
# 注意：/generate 必须注册在 /{set_id} 之前，否则会被路径参数吞掉


def _resolve_total(req: GenerateRequest) -> int:
    """按生成设置解析题量：显式 count 优先，简历通道回退到解析预估。"""
    settings = req.settings or {}
    count = settings.get("count")
    if isinstance(count, str):
        count = COUNT_LIMITS.get(count, DEFAULT_COUNT)
    if isinstance(count, int) and count > 0:
        return min(count, MAX_COUNT)

    analysis = store.state.resume_analysis
    if req.source == "resume" and analysis:
        estimated = int(analysis.get("estimatedCount") or DEFAULT_COUNT)
        return min(estimated, MAX_COUNT)
    return DEFAULT_COUNT


def _resolve_set(req: GenerateRequest) -> dict:
    """确定本次生成的目标题集：显式 setId > 同岗位已有题集 > 新建。"""
    settings = req.settings or {}
    set_id = settings.get("setId")
    if isinstance(set_id, str) and set_id:
        target = next((s for s in store.state.sets if s["id"] == set_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="目标题集不存在")
        return target

    analysis = store.state.resume_analysis or {}
    role = str(analysis.get("targetRole") or "通用岗位")
    title = f"{role} · {'简历专属题集' if req.source == 'resume' else '专属题集'}"
    existed = next(
        (s for s in store.state.sets if s["source"] == req.source and s["title"] == title), None
    )
    if existed:
        return existed

    new_set = {
        "id": store.new_id("set"),
        "source": req.source,
        "title": title,
        "questionCount": 0,
        "updatedAt": store.now_str(),
    }
    store.add_set(new_set)
    return new_set


@router.post("/generate", response_model=GenerateTaskOut)
async def start_generate(body: GenerateRequest | None = None) -> GenerateTaskOut:
    """触发出题（题量 = AI 解析预估，40/80/120 档，预计 3~10 分钟），返回 task_id。

    进度走 SSE `/{task_id}/stream` 或轮询 `/{task_id}/progress`。
    """
    req = body or GenerateRequest()
    total = _resolve_total(req)
    target_set = _resolve_set(req)
    task = store.GenerateTask(task_id=store.new_id("gen"), total=total)
    store.state.generate_tasks[task.task_id] = task
    generation.start(task, target_set["id"])
    return GenerateTaskOut(taskId=task.task_id)


def _progress_of(task: store.GenerateTask) -> dict:
    dimension = store.GENERATE_DIMENSIONS[
        min(task.dimension_index, len(store.GENERATE_DIMENSIONS) - 1)
    ]
    return {
        "generated": task.generated,
        "total": task.total,
        "currentDimension": dimension,
        "done": task.done,
        "dropped": task.dropped,
        "error": task.error,
        "setId": task.set_id,
    }


def _require_task(task_id: str) -> store.GenerateTask:
    task = store.state.generate_tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="出题任务不存在")
    return task


async def _stream(task: store.GenerateTask):
    """SSE：观察后台任务状态变化并推送，完成时发送 done 事件。"""
    last_sent = -1
    while True:
        if task.generated != last_sent:
            last_sent = task.generated
            payload = json.dumps(_progress_of(task), ensure_ascii=False)
            yield f"data: {payload}\n\n"
        if task.done:
            break
        await asyncio.sleep(0.05)
    yield "event: done\ndata: {}\n\n"


@router.get("/{task_id}/stream")
def stream_generate(task_id: str) -> StreamingResponse:
    task = _require_task(task_id)
    return StreamingResponse(
        _stream(task),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{task_id}/progress")
def generate_progress(task_id: str) -> dict:
    """轮询兜底接口（前端 SSE 断开重连时使用）。"""
    return _progress_of(_require_task(task_id))


# ---------------- 单个题集 ----------------


@router.get("/{set_id}", response_model=QuestionSet)
def get_set(set_id: str) -> dict:
    for s in store.state.sets:
        if s["id"] == set_id:
            return s
    raise HTTPException(status_code=404, detail="题集不存在")


@router.delete("/{set_id}")
def delete_set(set_id: str) -> dict:
    """删除题集：同步清理该题集的刷题进度与相关错题。"""
    if not store.remove_set(set_id):
        raise HTTPException(status_code=404, detail="题集不存在")

    store.state.progress.get(USER_ID, {}).pop(set_id, None)
    if USER_ID in store.state.wrong_book:
        store.state.wrong_book[USER_ID] = [
            w for w in store.state.wrong_book[USER_ID] if w["setId"] != set_id
        ]
    return {"ok": True}


@router.get("/{set_id}/questions", response_model=list[Question])
def list_set_questions(set_id: str) -> list[dict]:
    if not any(s["id"] == set_id for s in store.state.sets):
        raise HTTPException(status_code=404, detail="题集不存在")
    return [q for q in store.all_questions() if q["setId"] == set_id]
