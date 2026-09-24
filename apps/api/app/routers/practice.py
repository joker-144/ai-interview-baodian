"""刷题：作答进度 / 判分提交（答错自动入错题本）/ 收藏 / 重置。

用户维度当前固定为演示账号 demo-user（一期 Mock，接 JWT 后从 token 解析）。
"""

from fastapi import APIRouter, HTTPException

from app import store
from app.schemas import SubmitRequest, SubmitResult

USER_ID = store.USER_ID

router = APIRouter(tags=["practice"])


def _find_question(question_id: str) -> dict:
    for q in store.QUESTIONS_SEED:
        if q["id"] == question_id:
            return q
    raise HTTPException(status_code=404, detail="题目不存在")


def _upsert_wrong_item(question: dict) -> None:
    """答错自动入错题本：已存在则刷新错因进度，否则新建。"""
    book = store.state.wrong_book.setdefault(USER_ID, [])
    item = next((w for w in book if w["questionId"] == question["id"]), None)
    if item:
        item["wrongCount"] += 1
        item["lastWrongAt"] = "刚刚"
        item["stage"] = 0
        item["reviewStreak"] = 0
        item["mastered"] = False
        item["nextReviewLabel"] = store.REVIEW_STAGE_LABELS[0]
        return
    book.append(
        {
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
    )


@router.get("/api/progress/{set_id}")
def get_progress(set_id: str) -> dict:
    answers = store.state.progress.get(USER_ID, {}).get(set_id, {})
    return {"answeredCount": len(answers), "answers": answers}


@router.post("/api/progress/reset")
def reset_progress(body: dict) -> dict:
    set_id = body.get("setId", "")
    store.state.progress.get(USER_ID, {}).pop(set_id, None)
    return {"ok": True}


@router.post("/api/practice/submit", response_model=SubmitResult)
def submit(body: SubmitRequest) -> SubmitResult:
    question = _find_question(body.questionId)
    if question["setId"] != body.setId:
        raise HTTPException(status_code=400, detail="题目不属于该题集")

    correct = body.choice == question["answer"]

    user_progress = store.state.progress.setdefault(USER_ID, {})
    answers = user_progress.setdefault(body.setId, {})
    answers[body.questionId] = body.choice

    auto_added = False
    if not correct:
        book = store.state.wrong_book.setdefault(USER_ID, [])
        already = any(w["questionId"] == question["id"] for w in book)
        _upsert_wrong_item(question)
        auto_added = not already

    return SubmitResult(correct=correct, answer=question["answer"], autoAddedToWrongBook=auto_added)


@router.get("/api/favorites")
def list_favorites() -> list[str]:
    return store.state.favorites.get(USER_ID, [])


@router.post("/api/favorites/{question_id}/toggle")
def toggle_favorite(question_id: str) -> dict:
    favorites = store.state.favorites.setdefault(USER_ID, [])
    if question_id in favorites:
        favorites.remove(question_id)
        return {"favorited": False}
    favorites.append(question_id)
    return {"favorited": True}
