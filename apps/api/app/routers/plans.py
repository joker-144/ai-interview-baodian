"""今日学习计划（二期，文档 3.1）：当日惰性生成 / 手动增删 / 勾选与打卡。

无定时器口径（延续一期）：当日首次 GET 时生成 3~5 项任务——先由规则从真实数据
（进行中题集 / 错题到期队列 / 简历产物）收集候选，再交轻量模型措辞排序，
LLM 失败或超时直接采用规则结果，保证首屏不被模型调用卡死。
打卡：当日完成 ≥ 2/3 时 streak +1（同日不重复计，users.last_checkin_date 兜底）。
"""

from datetime import datetime

import json

from fastapi import APIRouter, Depends, HTTPException

from app import llm, store
from app.auth import get_current_user
from app.schemas import PlanAddRequest, PlanListOut, PlanTask

router = APIRouter(prefix="/api/plans", tags=["plans"])

PLAN_TYPES = ("practice", "review", "interview", "jd_set", "resume_check")


def _today() -> str:
    return store.today_key()


def _build_list(user_id: str, source: str) -> PlanListOut:
    """统一出参：任务列表 + 完成度 + streak 横幅数据（打卡达成日回传给前端提示）。"""
    items = store.today_plans(user_id)
    profile = store.user_profile(user_id)
    done = sum(1 for p in items if p["done"])
    return PlanListOut(
        items=[PlanTask(**p) for p in items],
        doneCount=done,
        total=len(items),
        streak=int(profile.get("streak", 0)),
        checkedIn=profile.get("lastCheckinDate") == _today(),
        source=source,
    )


def _rule_candidates(user_id: str) -> list[dict]:
    """从真实数据收集任务候选（顺序即优先级）。"""
    candidates: list[dict] = []

    # 进行中题集 → 刷题任务（携带 refId，前端「开始今日刷题」直达题集）
    counts: dict[str, int] = {}
    for q in store.all_questions():
        counts[q["setId"]] = counts.get(q["setId"], 0) + 1
    sets_by_id = {s["id"]: s for s in store.state.sets}
    for set_id, answers in store.state.progress.get(user_id, {}).items():
        total = counts.get(set_id, 0)
        if total and len(answers) < total:
            title_set = sets_by_id.get(set_id)
            name = title_set["title"] if title_set else "进行中题集"
            remaining = min(total - len(answers), 20)
            candidates.append({
                "type": "practice",
                "title": f"{name} · 继续刷 {remaining} 题",
                "estMinutes": 15,
                "refId": set_id,
            })

    # 错题本当日到期队列 → 复习任务
    due = [
        w for w in store.state.wrong_book.get(user_id, [])
        if not w["mastered"] and w["nextReviewLabel"] == "今天"
    ]
    if due:
        candidates.append({
            "type": "review",
            "title": f"错题复习 · 今日到期 {len(due)} 题",
            "estMinutes": 10,
        })

    # 已有简历产物 → 体检任务（一次；随简历重新上传重新出现）
    resume = store.user_resume(user_id)
    if resume and resume.get("analysis"):
        candidates.append({
            "type": "resume_check",
            "title": "简历体检 · 查看能力评估与优化建议",
            "estMinutes": 5,
        })

    # 无任何进行中题集时的保底刷题任务
    if not any(c["type"] == "practice" for c in candidates):
        latest = store.state.sets[0]["id"] if store.state.sets else None
        candidates.append({
            "type": "practice",
            "title": "今日刷题 · 20 题",
            "estMinutes": 20,
            "refId": latest,
        })
    return candidates[:5]


def _ai_polish(user_id: str, candidates: list[dict]) -> list[dict] | None:
    """轻量模型对候选任务做措辞与排序；任何异常返回 None（调用方回落规则结果）。"""
    profile = store.user_profile(user_id)
    prompt_tasks = [
        {"type": c["type"], "title": c["title"], "estMinutes": c["estMinutes"]}
        for c in candidates
    ]
    messages = [
        {"role": "system", "content": (
            "你是学习规划助手。根据给定的今日任务候选，输出 3~5 项任务，"
            "为 JSON：{\"tasks\":[{\"type\":\"...\",\"title\":\"...\",\"estMinutes\":数字}]}。"
            "要求：type 必须原样保留候选中的取值；title 可优化措辞但不得改变任务对象，"
            "不超过 40 字；estMinutes 取 5~40 的整数；不得新增候选之外的 type。"
        )},
        {"role": "user", "content": (
            f"用户：目标岗位 {profile.get('targetRole', '未设置')}，工作年限 {profile.get('years', 0)}。"
            f"今日任务候选：{prompt_tasks}"
        )},
    ]
    try:
        data = llm.chat_sync("light", messages, temperature=0.2, max_tokens=800, thinking=False)
        tasks = json.loads(llm.strip_fence(data)).get("tasks")
        if not isinstance(tasks, list) or not tasks:
            return None
        ref_by_type: dict[str, str | None] = {}
        for c in candidates:
            ref_by_type.setdefault(c["type"], c.get("refId"))
        polished: list[dict] = []
        for t in tasks[:5]:
            if not isinstance(t, dict) or t.get("type") not in PLAN_TYPES:
                continue
            title = str(t.get("title", "")).strip()
            if not title:
                continue
            polished.append({
                "type": t["type"],
                "title": title[:60],
                "estMinutes": max(5, min(40, int(t.get("estMinutes", 15) or 15))),
                "refId": ref_by_type.get(t["type"]),
            })
        return polished or None
    except Exception:
        return None


def _ensure_today(user_id: str) -> str:
    """当日计划为空时生成（AI 措辞 → 规则兜底）；返回生成来源。"""
    items = store.today_plans(user_id)
    if items:
        return "saved"
    candidates = _rule_candidates(user_id)
    polished = _ai_polish(user_id, candidates)
    source = "ai"
    tasks = polished
    if tasks is None:
        tasks = candidates
        source = "rule"
    for t in tasks:
        item = {
            "id": store.new_id("plan"),
            "type": t["type"],
            "title": t["title"],
            "estMinutes": t["estMinutes"],
            "done": False,
            "autoGenerated": True,
            "refId": t.get("refId"),
        }
        items.append(item)
        store.persist_plan(user_id, item)
    return source


@router.get("", response_model=PlanListOut)
def list_plans(user_id: str = Depends(get_current_user)) -> PlanListOut:
    store.ensure_user(user_id)
    source = _ensure_today(user_id)
    return _build_list(user_id, source)


@router.post("", response_model=PlanListOut, status_code=201)
def add_plan(body: PlanAddRequest, user_id: str = Depends(get_current_user)) -> PlanListOut:
    store.ensure_user(user_id)
    item = {
        "id": store.new_id("plan"),
        "type": "practice",
        "title": body.title.strip(),
        "estMinutes": 15,
        "done": False,
        "autoGenerated": False,
        "refId": None,
    }
    store.today_plans(user_id).append(item)
    store.persist_plan(user_id, item)
    return _build_list(user_id, "manual")


@router.delete("/{plan_id}", response_model=PlanListOut)
def delete_plan(plan_id: str, user_id: str = Depends(get_current_user)) -> PlanListOut:
    items = store.today_plans(user_id)
    before = len(items)
    store.state.plans[user_id][_today()] = [p for p in items if p["id"] != plan_id]
    if len(store.today_plans(user_id)) == before:
        raise HTTPException(status_code=404, detail="计划不存在")
    store.drop_plan(user_id, plan_id)
    return _build_list(user_id, "manual")


@router.post("/{plan_id}/toggle", response_model=PlanListOut)
def toggle_plan(plan_id: str, user_id: str = Depends(get_current_user)) -> PlanListOut:
    item = next((p for p in store.today_plans(user_id) if p["id"] == plan_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="计划不存在")
    item["done"] = not item["done"]
    store.persist_plan(user_id, item)

    # 打卡联动：当日完成 ≥ 2/3 且今日未记过 → streak +1（同日不重复计）
    items = store.today_plans(user_id)
    done = sum(1 for p in items if p["done"])
    checked_just_now = False
    if items and done * 3 >= len(items) * 2:
        overlay = store.state.profiles.setdefault(user_id, {})
        profile = store.user_profile(user_id)
        if profile.get("lastCheckinDate") != _today():
            overlay["streak"] = int(profile.get("streak", 0)) + 1
            overlay["lastCheckinDate"] = _today()
            store.persist_user(user_id)
            checked_just_now = True
    result = _build_list(user_id, "manual")
    if checked_just_now:
        # 前端据此弹「打卡成功」提示（streak 已在列表数据中）
        result.source = "checked_in"
    return result
