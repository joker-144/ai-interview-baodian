"""题集与出题任务（文档 4.5：统一在 `/api/question-sets` 命名空间）。

一期 Mock 实现：
- 题集 = 内存种子 + 用户自建（删除时级联清理刷题进度与错题）；
- 出题任务由**独立后台协程**推进，SSE 端点只做观察者——客户端断开不影响生成，
  轮询兜底接口可继续拿到进度；
- 引擎 B（岗位检索）/ 引擎 C（JD 定向）二期接入时，仅需在 start_generate 内
  按 `source` 分派到不同流水线，接口契约不变。
"""

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app import store
from app.schemas import (
    GenerateRequest,
    GenerateTaskOut,
    Question,
    QuestionSet,
    SetCreateRequest,
)

USER_ID = store.USER_ID
STEP_INTERVAL = 0.12  # 秒/步，模拟流式出题节奏

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
        "updatedAt": "刚刚创建",
    }
    store.state.sets.insert(0, new_set)
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
        return min(estimated, DEFAULT_COUNT)
    return DEFAULT_COUNT


def _create_task(total: int) -> store.GenerateTask:
    task = store.GenerateTask(task_id=store.new_id("gen"), total=total)
    store.state.generate_tasks[task.task_id] = task
    return task


async def _advance_task(task: store.GenerateTask) -> None:
    """独立后台推进出题进度：SSE 断开或轮询兜底时生成不中断。"""
    while not task.done:
        with task.lock:
            if not task.done:
                task.generated += 1
                task.dimension_index = task.generated * len(store.GENERATE_DIMENSIONS) // task.total
                if task.generated >= task.total:
                    task.done = True
        await asyncio.sleep(STEP_INTERVAL)


@router.post("/generate", response_model=GenerateTaskOut)
async def start_generate(body: GenerateRequest | None = None) -> GenerateTaskOut:
    """触发出题，返回 task_id（进度走 SSE `/{task_id}/stream` 或轮询 `/{task_id}/progress`）。"""
    req = body or GenerateRequest()
    task = _create_task(_resolve_total(req))
    # 句柄存到任务对象上，防止协程被 GC
    task.asyncio_handle = asyncio.create_task(_advance_task(task))
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
    before = len(store.state.sets)
    store.state.sets = [s for s in store.state.sets if s["id"] != set_id]
    if len(store.state.sets) == before:
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
    return [q for q in store.QUESTIONS_SEED if q["setId"] == set_id]
