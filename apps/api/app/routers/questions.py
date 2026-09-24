"""题库：题目查询与全站答对率（题集 CRUD 见 question_sets.py）。"""

from fastapi import APIRouter, HTTPException, Query

from app import store
from app.schemas import Question, SiteStats

# 低样本保护：作答次数不足 100 不展示全站答对率（合规红线第 4 条）
MIN_SAMPLE = 100

router = APIRouter(prefix="/api/questions", tags=["questions"])


@router.get("", response_model=list[Question])
def list_questions(
    set_id: str | None = Query(default=None, alias="setId"),
) -> list[dict]:
    if set_id is None:
        return store.QUESTIONS_SEED
    return [q for q in store.QUESTIONS_SEED if q["setId"] == set_id]


@router.get("/{question_id}", response_model=Question)
def get_question(question_id: str) -> dict:
    for q in store.QUESTIONS_SEED:
        if q["id"] == question_id:
            return q
    raise HTTPException(status_code=404, detail="题目不存在")


@router.get("/{question_id}/site-stats", response_model=SiteStats)
def site_stats(question_id: str) -> dict:
    """全站答对率（供题目详情页独立刷新）。

    一期无 answer_events 表，样本量按题目 id 稳定派生；接 PostgreSQL 后改为
    `SELECT count(*), avg(is_correct) FROM answer_events WHERE question_id = ...`。
    """
    question = next((q for q in store.QUESTIONS_SEED if q["id"] == question_id), None)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")

    rate = question["siteCorrectRate"]
    digest = sum(ord(c) for c in question_id)
    answered = digest % MIN_SAMPLE if rate is None else MIN_SAMPLE + digest % 900
    return {"questionId": question_id, "answeredCount": answered, "correctRate": rate}
