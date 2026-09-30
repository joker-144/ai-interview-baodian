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

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app import db, generation, store
from app.auth import get_current_user
from app.schemas import (
    GenerateRequest,
    GenerateTaskOut,
    Question,
    QuestionSet,
    SetCreateRequest,
)

# 题量三档口径（文档 6.x：精简 40 / 标准 80 / 深度 120，默认 80）
COUNT_LIMITS = {"lite": 40, "standard": 80, "deep": 120}
DEFAULT_COUNT = 80
# 引擎 B（岗位检索）默认题量（文档 3.5：默认 50）
JOB_SEARCH_DEFAULT_COUNT = 50
# 引擎 C（子集）单岗位专属题默认题量（单 JD 素材少于市场级）
JD_TARGET_DEFAULT_COUNT = 30
MAX_COUNT = max(COUNT_LIMITS.values())

router = APIRouter(prefix="/api/question-sets", tags=["question_sets"])


# ---------------- 题集 CRUD ----------------


@router.get("", response_model=list[QuestionSet])
def list_sets(user_id: str = Depends(get_current_user)) -> list[dict]:
    """题库列表：公共题库 + 本人私有题集（按 ownerId 隔离，不见他人题集）。"""
    return store.visible_sets(user_id)


@router.post("", response_model=QuestionSet, status_code=201)
def create_set(body: SetCreateRequest, user_id: str = Depends(get_current_user)) -> dict:
    """新建自定义题集（初始为空，题目由引擎生成或手动加入；归属当前用户，私有可见）。"""
    new_set = {
        "id": store.new_id("set"),
        "source": body.source,
        "ownerId": user_id,
        "title": body.title,
        "questionCount": 0,
        "updatedAt": store.now_str(),
    }
    store.add_set(new_set)
    return new_set


# ---------------- 出题任务（引擎 A/B/C 统一入口） ----------------
# 注意：/generate 必须注册在 /{set_id} 之前，否则会被路径参数吞掉


def _resolve_total(req: GenerateRequest, user_id: str) -> int:
    """按生成设置解析题量：显式 count 优先，岗位检索默认 50，简历通道回退到解析预估。"""
    settings = req.settings or {}
    count = settings.get("count")
    if isinstance(count, str):
        count = COUNT_LIMITS.get(count, DEFAULT_COUNT)
    if isinstance(count, int) and count > 0:
        return min(count, MAX_COUNT)

    if req.source == "job_search":
        return JOB_SEARCH_DEFAULT_COUNT
    if req.source == "jd_target":
        return JD_TARGET_DEFAULT_COUNT

    analysis = (store.user_resume(user_id) or {}).get("analysis")
    if req.source == "resume" and analysis:
        estimated = int(analysis.get("estimatedCount") or DEFAULT_COUNT)
        return min(estimated, MAX_COUNT)
    return DEFAULT_COUNT


def _resolve_set(req: GenerateRequest, user_id: str) -> dict:
    """确定本次生成的目标题集：显式 setId > 同岗位已有题集 > 新建。"""
    settings = req.settings or {}
    set_id = settings.get("setId")
    if isinstance(set_id, str) and set_id:
        if not store.can_access_set(set_id, user_id):
            raise HTTPException(status_code=404, detail="目标题集不存在")
        return store.find_set(set_id)

    if req.source == "job_search":
        # 岗位市场题集按关键词+城市归并（同关键词重生成落到同一题集）
        keyword = str(settings.get("keyword") or "").strip()
        title = f"{keyword or '岗位市场'} · 岗位市场题集"
    elif req.source == "jd_target":
        # 单岗位专属题集按岗位名归并（同岗位重生成落到同一题集）
        job_name = str(settings.get("jobName") or "").strip()
        title = f"{job_name or '单岗位'} · 岗位专属预测题"
    else:
        analysis = (store.user_resume(user_id) or {}).get("analysis") or {}
        role = str(analysis.get("targetRole") or "通用岗位")
        title = f"{role} · {'简历专属题集' if req.source == 'resume' else '专属题集'}"

    existed = next(
        (s for s in store.state.sets if s["source"] == req.source and s["title"] == title
         and s.get("ownerId") == user_id), None
    )
    if existed:
        return existed

    new_set = {
        "id": store.new_id("set"),
        "source": req.source,
        "ownerId": user_id,
        "title": title,
        "questionCount": 0,
        "updatedAt": store.now_str(),
    }
    store.add_set(new_set)
    return new_set


def _resolve_job_name(user_id: str, security_id: str, settings: dict) -> str:
    """单岗位专属题的岗位名：优先本人看板卡，回退检索缓存回填。"""
    card = next(
        (c for c in store.pipeline_cards(user_id) if c.get("securityId") == security_id),
        None,
    )
    if card and card.get("jobName"):
        return str(card["jobName"])
    keyword = str(settings.get("keyword") or "").strip()
    city = str(settings.get("city") or "").strip()
    if keyword:
        cached = store.jobs_cache_get(store.jobs_cache_key(keyword, city))
        for item in (cached or {}).get("payload") or []:
            if item.get("securityId") == security_id and item.get("jobName"):
                return str(item["jobName"])
    return ""


@router.post("/generate", response_model=GenerateTaskOut)
async def start_generate(
    body: GenerateRequest | None = None, user_id: str = Depends(get_current_user)
) -> GenerateTaskOut:
    """触发出题（题量 = AI 解析预估，40/80/120 档，预计 3~10 分钟），返回 task_id。

    进度走 SSE `/{task_id}/stream` 或轮询 `/{task_id}/progress`。
    """
    req = body or GenerateRequest()
    total = _resolve_total(req, user_id)

    # 引擎 C（子集）单岗位专属题：securityId 必填；提前解析 jobName 供题集标题与看板挂接
    jd_security_id = ""
    jd_job_name = ""
    if req.source == "jd_target":
        settings = req.settings or {}
        jd_security_id = str(settings.get("securityId") or "").strip()
        if not jd_security_id:
            raise HTTPException(status_code=422, detail="单岗位专属出题需要提供 securityId（目标岗位）")
        jd_job_name = _resolve_job_name(user_id, jd_security_id, settings)
        req.settings = {**settings, "securityId": jd_security_id, "jobName": jd_job_name}

    target_set = _resolve_set(req, user_id)

    # 引擎 B（岗位检索）：settings 携带 keyword/city/difficulty/withAnswer，复用同一任务骨架
    if req.source == "job_search":
        settings = req.settings or {}
        keyword = str(settings.get("keyword") or "").strip()
        if not keyword:
            raise HTTPException(status_code=422, detail="岗位检索出题需要提供 keyword（目标岗位关键词）")
        # 未先检索就出题：目标题集已建、任务无岗位样本可用，直接提示引导
        map_key = store.jobs_cache_key(keyword, str(settings.get("city") or "").strip())
        if store.jobs_cache_get(map_key) is None:
            raise HTTPException(status_code=409, detail="请先在岗位页完成该关键词的检索，再生成市场题库")

    # 新任务发起即取代该用户此前的中断记录（active 查询不再返回旧中断横幅）
    if any(t.user_id == user_id and t.interrupted for t in store.state.generate_tasks.values()):
        for old in store.state.generate_tasks.values():
            if old.user_id == user_id and old.interrupted:
                old.interrupted = False
        db.dismiss_interrupted_tasks(user_id)  # 持久化取代，重启后不再复活
    task = store.GenerateTask(task_id=store.new_id("gen"), total=total, user_id=user_id)
    store.state.generate_tasks[task.task_id] = task
    db.create_generate_task(task.task_id, user_id, target_set["id"], total)  # 中断恢复锚点
    if req.source == "job_search":
        settings = req.settings or {}
        generation.start_job_search(
            task,
            target_set["id"],
            {
                "keyword": keyword,
                "city": str(settings.get("city") or "").strip(),
                "difficulty": str(settings.get("difficulty") or "混合"),
                "withAnswer": bool(settings.get("withAnswer", True)),
            },
        )
    elif req.source == "jd_target":
        # 立即挂接看板卡：使看板卡即刻显示该专属题集与进度（融合闭环）
        for card in store.pipeline_cards(user_id):
            if card.get("securityId") == jd_security_id and not card.get("inTrash"):
                card["setId"] = target_set["id"]
                store.pipeline_save(user_id, card)
                break
        settings = req.settings or {}
        generation.start_job_detail(
            task,
            target_set["id"],
            {
                "securityId": jd_security_id,
                "keyword": str(settings.get("keyword") or "").strip(),
                "city": str(settings.get("city") or "").strip(),
                "jobName": jd_job_name,
                "difficulty": str(settings.get("difficulty") or "混合"),
                "withAnswer": bool(settings.get("withAnswer", True)),
            },
        )
    else:
        generation.start(task, target_set["id"])
    return GenerateTaskOut(taskId=task.task_id)


@router.get("/generate/active")
def active_generate(user_id: str = Depends(get_current_user)) -> dict:
    """当前用户最近的未完成出题任务（页面刷新 / 断线后恢复进度用）。

    - running：前端重新订阅 SSE `/{task_id}/stream` 即可恢复进度条；
    - interrupted：服务重启导致的中断，提示「继续补齐剩余题目」
      （剩余量 = total - generated，带 setId 续作，avoid 已有题干天然去重）；
    - 无活跃任务：taskId 为 null。
    """
    task = store.active_generate_task(user_id)
    if task is None:
        return {"taskId": None}
    return {"taskId": task.task_id, **_progress_of(task)}


def _progress_of(task: store.GenerateTask) -> dict:
    dimension = store.GENERATE_DIMENSIONS[
        min(task.dimension_index, len(store.GENERATE_DIMENSIONS) - 1)
    ]
    return {
        "taskId": task.task_id,
        "generated": task.generated,
        "total": task.total,
        "currentDimension": dimension,
        "done": task.done,
        "dropped": task.dropped,
        "error": task.error,
        "setId": task.set_id,
        "interrupted": task.interrupted,
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
def get_set(set_id: str, user_id: str = Depends(get_current_user)) -> dict:
    if not store.can_access_set(set_id, user_id):
        raise HTTPException(status_code=404, detail="题集不存在")
    return store.find_set(set_id)


@router.delete("/{set_id}")
def delete_set(set_id: str, user_id: str = Depends(get_current_user)) -> dict:
    """删除题集：仅限本人私有题集；同步清理该用户在该题集的刷题进度与相关错题。"""
    target = store.find_set(set_id)
    if target is None:
        raise HTTPException(status_code=404, detail="题集不存在")
    if target.get("ownerId") != user_id:
        # 公共题库（预置种子）与他人私有题集不可删（越权防护）
        raise HTTPException(status_code=403, detail="无权删除该题集（公共题库或他人题集）")
    if not store.remove_set(set_id):
        raise HTTPException(status_code=404, detail="题集不存在")

    store.state.progress.get(user_id, {}).pop(set_id, None)
    if user_id in store.state.wrong_book:
        store.state.wrong_book[user_id] = [
            w for w in store.state.wrong_book[user_id] if w["setId"] != set_id
        ]
    store.persist_set_deletion(user_id, set_id)
    return {"ok": True}


@router.get("/{set_id}/questions", response_model=list[Question])
def list_set_questions(set_id: str, user_id: str = Depends(get_current_user)) -> list[dict]:
    if not store.can_access_set(set_id, user_id):
        raise HTTPException(status_code=404, detail="题集不存在")
    return [q for q in store.all_questions() if q["setId"] == set_id]
