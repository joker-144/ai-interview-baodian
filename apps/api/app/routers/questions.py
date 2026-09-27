"""题库：题目查询与全站答对率（题集 CRUD 见 question_sets.py）。"""

from fastapi import APIRouter, HTTPException, Query

from app import store
from app.schemas import Question, SiteStats

router = APIRouter(prefix="/api/questions", tags=["questions"])


@router.get("", response_model=list[Question])
def list_questions(
    set_id: str | None = Query(default=None, alias="setId"),
) -> list[dict]:
    if set_id is None:
        return store.all_questions()
    return [q for q in store.all_questions() if q["setId"] == set_id]


@router.get("/{question_id}", response_model=Question)
def get_question(question_id: str) -> dict:
    question = store.find_question(question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    return question


@router.get("/{question_id}/site-stats", response_model=SiteStats)
def site_stats(question_id: str) -> dict:
    """全站答对率（供题目详情页独立刷新）。

    真实口径：answer_events 按题聚合（内存 answer_stats，提交时实时更新），
    样本量 ≥ MIN_SAMPLE 时返回真实作答数与答对率；未达标时维持演示口径——
    种子题按题目 id 稳定派生样本量，引擎生成的题（siteCorrectRate=None）
    按低样本保护不展示。
    """
    question = store.find_question(question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")

    stats = store.state.answer_stats.get(question_id)
    if stats and stats["attempts"] >= store.MIN_SAMPLE:
        return {
            "questionId": question_id,
            "answeredCount": stats["attempts"],
            "correctRate": question["siteCorrectRate"],
        }

    rate = question["siteCorrectRate"]
    digest = sum(ord(c) for c in question_id)
    answered = digest % store.MIN_SAMPLE if rate is None else store.MIN_SAMPLE + digest % 900
    return {"questionId": question_id, "answeredCount": answered, "correctRate": rate}
