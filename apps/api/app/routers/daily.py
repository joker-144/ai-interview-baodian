"""每日一练（三期，文档 3.8）：每天 10 题（薄弱知识点 60% + 随机 40%）。

- 当日首次访问惰性生成（无定时器口径，不调 LLM）；题库题量不足时有多少给多少；
- 判分完全复用 POST /api/practice/submit（传题目原 setId，进度 / 错题 / 全站统计
  自然打通），本路由只维护「当日完成清单」；
- 全部完成计入连续打卡（streak + lastCheckinDate 同日去重，与今日学习计划口径一致）。
"""

import random

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import store
from app.auth import get_current_user

DAILY_SIZE = 10
WEAK_RATIO = 0.6  # 薄弱知识点占比（文档 3.8：薄弱 60% + 随机 40%）

router = APIRouter(prefix="/api/daily-practice", tags=["daily"])


class DailyProgressRequest(BaseModel):
    questionId: str


def _today() -> str:
    return store.today_key()


def _pick_questions(user_id: str) -> list[dict]:
    """薄弱 60%：错题本未掌握项优先，不足用其知识点关联题补；其余随机补足。"""
    pool = list(store.visible_questions(user_id))
    random.shuffle(pool)

    weak_target = round(DAILY_SIZE * WEAK_RATIO)
    wrong = store.state.wrong_book.get(user_id, [])
    unmastered = {w["questionId"] for w in wrong if not w.get("mastered")}

    weak = [q for q in pool if q["id"] in unmastered]
    weak_tags: set[str] = set()
    for q in weak:
        weak_tags.update(q.get("knowledgeTags") or [])
    related = [
        q for q in pool
        if q["id"] not in unmastered and (set(q.get("knowledgeTags") or []) & weak_tags)
    ]

    picked: list[dict] = []
    chosen: set[str] = set()
    for group in (weak, related):
        for q in group:
            if len(picked) >= weak_target:
                break
            if q["id"] in chosen:
                continue
            picked.append(q)
            chosen.add(q["id"])
    for q in pool:  # 随机 40%：全库乱序补足（已在薄弱池的题天然跳过）
        if len(picked) >= DAILY_SIZE:
            break
        if q["id"] in chosen:
            continue
        picked.append(q)
        chosen.add(q["id"])
    return picked


def _ensure_practice(user_id: str) -> dict:
    """当日记录（惰性生成：首次访问落一次题目快照，当日不再变化）。"""
    record = store.daily_get(user_id, _today())
    if record is not None:
        return record
    questions = _pick_questions(user_id)
    record = {
        "id": store.new_id("daily"),
        "date": _today(),
        "questionIds": [q["id"] for q in questions],
        "doneIds": [],
    }
    store.daily_put(user_id, record)
    return record


def _payload(user_id: str) -> dict:
    record = _ensure_practice(user_id)
    questions = [
        q
        for q in (store.find_question(qid) for qid in record["questionIds"])
        if q is not None
    ]
    profile = store.user_profile(user_id)
    return {
        "date": record["date"],
        "total": len(record["questionIds"]),
        "doneIds": record["doneIds"],
        "questions": questions,
        "streak": int(profile.get("streak", 0)),
        "checkedInToday": profile.get("lastCheckinDate") == _today(),
    }


@router.get("")
def get_daily_practice(user_id: str = Depends(get_current_user)) -> dict:
    """当日每日一练（首次访问惰性生成 10 题，薄弱 60% + 随机 40%）。"""
    return _payload(user_id)


@router.post("/progress")
def mark_daily_progress(
    body: DailyProgressRequest, user_id: str = Depends(get_current_user)
) -> dict:
    """标记一题完成（判分本体走 /api/practice/submit）；全部完成触发打卡。"""
    record = _ensure_practice(user_id)
    if body.questionId not in record["questionIds"]:
        raise HTTPException(status_code=400, detail="该题不在今日一练清单中")
    if body.questionId not in record["doneIds"]:
        record["doneIds"].append(body.questionId)
        store.daily_put(user_id, record)

    completed = len(record["doneIds"]) >= len(record["questionIds"])
    checked_in = False
    if completed:
        # 打卡联动：全部完成且今日未记过 → streak +1（同日不重复计）
        overlay = store.state.profiles.setdefault(user_id, {})
        profile = store.user_profile(user_id)
        if profile.get("lastCheckinDate") != _today():
            overlay["streak"] = int(profile.get("streak", 0)) + 1
            overlay["lastCheckinDate"] = _today()
            store.persist_user(user_id)
            checked_in = True
    return {
        "doneCount": len(record["doneIds"]),
        "total": len(record["questionIds"]),
        "completed": completed,
        "checkedIn": checked_in,
    }
