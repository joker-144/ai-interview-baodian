"""错题本：错因三分法 + 艾宾浩斯五档队列 + 连对归档（按 JWT 用户隔离）。"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app import store
from app.auth import get_current_user
from app.schemas import (
    ReviewQueueItem,
    ReviewSubmitRequest,
    ReviewSubmitResult,
    WrongAddRequest,
    WrongItem,
    WrongReason,
    WrongReasonUpdate,
)

router = APIRouter(prefix="/api/wrong-book", tags=["wrong_book"])


def _book(user_id: str) -> list[dict]:
    return store.state.wrong_book.setdefault(user_id, [])


def _find_item(user_id: str, question_id: str) -> dict:
    for w in _book(user_id):
        if w["questionId"] == question_id:
            return w
    raise HTTPException(status_code=404, detail="错题不存在")


@router.get("", response_model=list[WrongItem])
def list_wrong(
    reason: WrongReason | None = Query(default=None),
    user_id: str = Depends(get_current_user),
) -> list[dict]:
    items = [w for w in _book(user_id) if not w["mastered"]]
    if reason:
        items = [w for w in items if w["reason"] == reason]
    return items


@router.get("/stats")
def wrong_stats(user_id: str = Depends(get_current_user)) -> dict:
    items = [w for w in _book(user_id) if not w["mastered"]]
    return {
        "pending": len(items),
        "dueToday": sum(1 for w in items if w["nextReviewLabel"] == "今天"),
        "mastered": store.MASTERED_BASE + sum(1 for w in _book(user_id) if w["mastered"]),
    }


@router.get("/review-queue", response_model=list[ReviewQueueItem])
def review_queue(user_id: str = Depends(get_current_user)) -> list[ReviewQueueItem]:
    """艾宾浩斯五档队列：按未掌握错题当前所处阶段聚合。"""
    active = [w for w in _book(user_id) if not w["mastered"]]
    return [
        ReviewQueueItem(label=label, count=sum(1 for w in active if w["nextReviewLabel"] == label))
        for label in store.REVIEW_STAGE_LABELS
    ]


@router.post("", response_model=WrongItem, status_code=201)
def add_wrong(body: WrongAddRequest, user_id: str = Depends(get_current_user)) -> dict:
    item = next((w for w in _book(user_id) if w["questionId"] == body.questionId), None)
    if item:
        item["reason"] = body.reason  # 已存在则仅更新错因（手动强化入口）
        store.persist_wrong_item(user_id, item)
        return item

    question = store.find_question(body.questionId)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")

    item = {
        "questionId": question["id"],
        "setId": question["setId"],
        "reason": body.reason,
        "wrongCount": 1,
        "lastWrongAt": "刚刚",
        "stage": 0,
        "nextReviewLabel": store.REVIEW_STAGE_LABELS[0],
        "mastered": False,
        "reviewStreak": 0,
    }
    _book(user_id).append(item)
    store.persist_wrong_item(user_id, item)
    return item


@router.patch("/{question_id}/reason", response_model=WrongItem)
def update_reason(
    question_id: str, body: WrongReasonUpdate, user_id: str = Depends(get_current_user)
) -> dict:
    item = _find_item(user_id, question_id)
    item["reason"] = body.reason
    store.persist_wrong_item(user_id, item)
    return item


@router.post("/review", response_model=ReviewSubmitResult)
def submit_review(
    body: ReviewSubmitRequest, user_id: str = Depends(get_current_user)
) -> ReviewSubmitResult:
    item = _find_item(user_id, body.questionId)
    if item["mastered"]:
        return ReviewSubmitResult(mastered=True)

    if body.correct:
        item["reviewStreak"] += 1
        # 连对 3 次或已到顶档（第 15 天）→ 归档「已掌握」
        if item["reviewStreak"] >= 3 or item["stage"] >= len(store.REVIEW_STAGE_LABELS) - 1:
            item["mastered"] = True
        else:
            item["stage"] += 1
            item["nextReviewLabel"] = store.REVIEW_STAGE_LABELS[item["stage"]]
    else:
        # 答错回退到第一档重新开始
        item["stage"] = 0
        item["reviewStreak"] = 0
        item["wrongCount"] += 1
        item["lastWrongAt"] = "刚刚"
        item["nextReviewLabel"] = store.REVIEW_STAGE_LABELS[0]
    store.persist_wrong_item(user_id, item)
    return ReviewSubmitResult(mastered=item["mastered"])
