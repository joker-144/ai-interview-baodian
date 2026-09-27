"""刷题：作答进度 / 判分提交（答错自动入错题本）/ 收藏 / 重置。

所有端点按 JWT 识别当前用户（get_current_user），进度 / 错题 / 收藏按 user_id 隔离。
"""

from fastapi import APIRouter, Depends, HTTPException

from app import store
from app.auth import get_current_user
from app.schemas import SubmitRequest, SubmitResult

router = APIRouter(tags=["practice"])


def _find_question(question_id: str) -> dict:
    question = store.find_question(question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    return question


def _upsert_wrong_item(user_id: str, question: dict) -> dict:
    """答错自动入错题本：已存在则刷新错因进度，否则新建。返回条目（供落库）。"""
    book = store.state.wrong_book.setdefault(user_id, [])
    item = next((w for w in book if w["questionId"] == question["id"]), None)
    if item:
        item["wrongCount"] += 1
        item["lastWrongAt"] = "刚刚"
        item["stage"] = 0
        item["reviewStreak"] = 0
        item["mastered"] = False
        item["nextReviewLabel"] = store.REVIEW_STAGE_LABELS[0]
        return item
    item = {
        "questionId": question["id"],
        "setId": question["setId"],
        "reason": "concept",  # 默认错因，可在错题本/详情页修改
        "wrongCount": 1,
        "lastWrongAt": "刚刚",
        "stage": 0,
        "nextReviewLabel": store.REVIEW_STAGE_LABELS[0],
        "mastered": False,
        "reviewStreak": 0,
    }
    book.append(item)
    return item


@router.get("/api/progress/{set_id}")
def get_progress(set_id: str, user_id: str = Depends(get_current_user)) -> dict:
    answers = store.state.progress.get(user_id, {}).get(set_id, {})
    return {"answeredCount": len(answers), "answers": answers}


@router.post("/api/progress/reset")
def reset_progress(body: dict, user_id: str = Depends(get_current_user)) -> dict:
    set_id = body.get("setId", "")
    store.state.progress.get(user_id, {}).pop(set_id, None)
    store.persist_progress_reset(user_id, set_id)
    return {"ok": True}


@router.post("/api/practice/submit", response_model=SubmitResult)
def submit(body: SubmitRequest, user_id: str = Depends(get_current_user)) -> SubmitResult:
    question = _find_question(body.questionId)
    if question["setId"] != body.setId:
        raise HTTPException(status_code=400, detail="题目不属于该题集")

    correct = body.choice == question["answer"]

    user_progress = store.state.progress.setdefault(user_id, {})
    answers = user_progress.setdefault(body.setId, {})
    answers[body.questionId] = body.choice
    store.persist_answer(user_id, body.setId, body.questionId, body.choice)

    # 全站答对率：真实作答事件聚合（样本量 ≥ store.MIN_SAMPLE 时回写展示值）
    store.record_answer_event(user_id, body.setId, question, body.choice, correct)

    auto_added = False
    if not correct:
        book = store.state.wrong_book.setdefault(user_id, [])
        already = any(w["questionId"] == question["id"] for w in book)
        item = _upsert_wrong_item(user_id, question)
        store.persist_wrong_item(user_id, item)
        auto_added = not already

    return SubmitResult(correct=correct, answer=question["answer"], autoAddedToWrongBook=auto_added)


@router.get("/api/favorites")
def list_favorites(user_id: str = Depends(get_current_user)) -> list[str]:
    return store.state.favorites.get(user_id, [])


@router.post("/api/favorites/{question_id}/toggle")
def toggle_favorite(question_id: str, user_id: str = Depends(get_current_user)) -> dict:
    favorites = store.state.favorites.setdefault(user_id, [])
    if question_id in favorites:
        favorites.remove(question_id)
        store.persist_favorite(user_id, question_id, False)
        return {"favorited": False}
    favorites.append(question_id)
    store.persist_favorite(user_id, question_id, True)
    return {"favorited": True}
