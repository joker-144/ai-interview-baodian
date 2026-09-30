"""模拟考试（P11，二期批 2）：组卷、逐题作答增量落库、暂停/恢复、判分与模考报告。

- 组卷：指定题集抽客观题（single/multi/judge）最多 store.EXAM_MAX_QUESTIONS 题，
  限时 = 题数 × store.EXAM_SEC_PER_QUESTION；岗位分桶取 target_role（空则通用桶）。
- 恢复：作答逐题覆盖写 answered_detail（断网/刷新后 GET 原样返回），question_ids
  为组卷快照，不随题库变动；running 记录 GET /{id} 原样返回由前端弹「恢复上次考试」。
- 暂停口径（Web）：visibilitychange hidden 超 10 分钟前端才调 pause；后端只累计
  paused_sec 与有效作答用时 duration_sec（不含暂停）。
- 报告：同岗位分桶百分位为真实聚合（样本 < 5 返回 null，前端显示「样本积累中」）。
"""

import json
import random
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from app import store
from app.auth import get_current_user
from app.routers.practice import _upsert_wrong_item
from app.schemas import ExamAnswerRequest, ExamCreateRequest

router = APIRouter(prefix="/api/exams", tags=["exams"])

EXAM_TIME_FMT = "%Y-%m-%d %H:%M:%S"
PERCENTILE_MIN_SAMPLE = 5   # 分桶样本下限：不足时不给百分位
REMEDIAL_MAX = 10           # 补强练习卷题量上限


def _now() -> str:
    return datetime.now().strftime(EXAM_TIME_FMT)


def _parse(ts: str | None) -> float | None:
    try:
        return datetime.strptime(ts, EXAM_TIME_FMT).timestamp() if ts else None
    except ValueError:
        return None


def _elapsed_sec(exam: dict) -> int:
    """当前有效作答秒数 = 已累计 durationSec + 本次活跃段（暂停中不计）。"""
    active_since = _parse(exam.get("activeSince"))
    if active_since is None:
        return int(exam.get("durationSec", 0))
    return int(exam.get("durationSec", 0)) + int(datetime.now().timestamp() - active_since)


def _exam_questions(exam: dict) -> list[dict]:
    """按组卷快照取题（题库可能已变动，缺题跳过）。"""
    by_id = {q["id"]: q for q in store.all_questions()}
    return [by_id[qid] for qid in exam.get("questionIds", []) if qid in by_id]


def _public_question(q: dict) -> dict:
    """考试态题目脱敏：不回传答案 / 解析（判分只在后端）。"""
    return {
        "id": q["id"], "setId": q["setId"], "type": q["type"], "category": q["category"],
        "stem": q["stem"], "options": q.get("options", []), "knowledgeTags": q.get("knowledgeTags", []),
        "difficulty": q.get("difficulty", 2),
    }


def _require(user_id: str, exam_id: str) -> dict:
    store.ensure_user(user_id)
    exam = store.get_exam(user_id, exam_id)
    if exam is None:
        raise HTTPException(status_code=404, detail="考试不存在")
    return exam


# ---------------- 组卷与恢复 ----------------


@router.post("", status_code=201)
def create_exam(body: ExamCreateRequest, user_id: str = Depends(get_current_user)) -> dict:
    store.ensure_user(user_id)
    if not store.can_access_set(body.setId, user_id):
        raise HTTPException(status_code=404, detail="题集不存在")

    pool = [
        q for q in store.all_questions()
        if q["setId"] == body.setId and q["type"] in store.EXAM_OBJECTIVE_TYPES
    ]
    if not pool:
        raise HTTPException(status_code=400, detail="该题集暂无客观题，无法组卷")
    random.shuffle(pool)
    picked = pool[: store.EXAM_MAX_QUESTIONS]

    profile = store.user_profile(user_id)
    now = _now()
    exam = {
        "id": store.new_id("exam"),
        "userId": user_id,
        "setId": body.setId,
        "bucketRole": profile.get("targetRole") or "通用",
        "total": len(picked),
        "questionIds": [q["id"] for q in picked],
        "answers": [],               # [{questionId, choice, timeSec, correct}]
        "score": None,
        "durationSec": 0,            # 有效作答用时（不含暂停），活跃时持续增长
        "pausedSec": 0,
        "status": "running",
        "activeSince": now,          # 内存运行态：当前活跃段起点（不落库，恢复从 durationSec 延续）
        "createdAt": now,
        "finishedAt": None,
    }
    store.state.exams[exam["id"]] = exam
    store.persist_exam(exam)
    return {
        "examId": exam["id"],
        "total": exam["total"],
        "durationSec": exam["total"] * store.EXAM_SEC_PER_QUESTION,
        "questions": [_public_question(q) for q in picked],
    }


@router.get("")
def list_exams(user_id: str = Depends(get_current_user)) -> list[dict]:
    """历史列表（含 running，前端据此弹「恢复上次考试」）。"""
    return [
        {
            "examId": e["id"], "setId": e["setId"], "bucketRole": e.get("bucketRole", ""),
            "total": e.get("total", 0), "answered": len(e.get("answers", [])),
            "score": e.get("score"), "durationSec": e.get("durationSec", 0),
            "status": e["status"], "createdAt": e.get("createdAt", ""),
            "finishedAt": e.get("finishedAt"),
        }
        for e in store.user_exams(user_id)
    ]


@router.get("/{exam_id}")
def get_exam(exam_id: str, user_id: str = Depends(get_current_user)) -> dict:
    """running/paused 原样返回（恢复基础）；done 返回概要（详情走 /report）。"""
    exam = _require(user_id, exam_id)
    questions = _exam_questions(exam)
    elapsed = _elapsed_sec(exam)
    limit = exam["total"] * store.EXAM_SEC_PER_QUESTION
    payload = {
        "examId": exam["id"], "setId": exam["setId"], "bucketRole": exam.get("bucketRole", ""),
        "total": exam["total"], "answered": len(exam.get("answers", [])),
        "score": exam.get("score"), "status": exam["status"],
        "elapsedSec": elapsed, "durationSec": limit,
        "createdAt": exam.get("createdAt", ""), "finishedAt": exam.get("finishedAt"),
        "questions": [_public_question(q) for q in questions],
        "answers": {
            a["questionId"]: {"choice": a["choice"], "timeSec": a.get("timeSec", 0)}
            for a in exam.get("answers", [])
        },
    }
    if exam["status"] in ("running", "paused") and elapsed >= limit:
        payload["expired"] = True  # 前端提示时间已到，引导交卷
    return payload


# ---------------- 作答 / 暂停 / 交卷 ----------------


@router.put("/{exam_id}/answer")
def answer(exam_id: str, body: ExamAnswerRequest, user_id: str = Depends(get_current_user)) -> dict:
    """单题作答：即时判分但不回传对错；增量落库（断网恢复基础）；
    与练习一致计入作答事件与错题本（模考错题同样进复习队列）。"""
    exam = _require(user_id, exam_id)
    if exam["status"] != "running":
        raise HTTPException(status_code=409, detail="考试不在进行中")
    if body.questionId not in set(exam.get("questionIds", [])):
        raise HTTPException(status_code=400, detail="题目不在本卷中")

    by_id = {q["id"]: q for q in store.all_questions()}
    question = by_id.get(body.questionId)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在（可能已被删除）")

    correct = body.choice == question["answer"]
    answers = [a for a in exam.get("answers", []) if a["questionId"] != body.questionId]
    answers.append({
        "questionId": body.questionId, "choice": body.choice,
        "timeSec": max(int(body.timeSec or 0), 0), "correct": correct,
    })
    exam["answers"] = answers
    exam["durationSec"] = _elapsed_sec(exam)  # 快照当前用时，恢复时以此延续

    # 统计口径与练习一致：全站答对率事件 + 错题入本
    store.record_answer_event(user_id, exam["setId"], question, body.choice, correct)
    if not correct:
        item = _upsert_wrong_item(user_id, question)
        store.persist_wrong_item(user_id, item)

    store.persist_exam(exam)
    return {"ok": True, "answered": len(answers)}


@router.post("/{exam_id}/pause")
def pause(exam_id: str, user_id: str = Depends(get_current_user)) -> dict:
    exam = _require(user_id, exam_id)
    if exam["status"] != "running":
        raise HTTPException(status_code=409, detail="仅进行中的考试可暂停")
    exam["durationSec"] = _elapsed_sec(exam)   # 结算活跃段
    exam["activeSince"] = None
    exam["pausedAt"] = _now()                  # 内存运行态：本次暂停起点
    exam["status"] = "paused"
    store.persist_exam(exam)
    return {"ok": True, "pausedSec": exam.get("pausedSec", 0)}


@router.post("/{exam_id}/resume")
def resume(exam_id: str, user_id: str = Depends(get_current_user)) -> dict:
    exam = _require(user_id, exam_id)
    if exam["status"] != "paused":
        raise HTTPException(status_code=409, detail="仅已暂停的考试可恢复")
    paused_at = _parse(exam.get("pausedAt"))
    if paused_at is not None:
        exam["pausedSec"] = int(exam.get("pausedSec", 0)) + int(
            datetime.now().timestamp() - paused_at
        )
    exam["pausedAt"] = None
    exam["activeSince"] = _now()
    exam["status"] = "running"
    store.persist_exam(exam)
    return {"ok": True}


@router.post("/{exam_id}/abandon")
def abandon(exam_id: str, user_id: str = Depends(get_current_user)) -> dict:
    exam = _require(user_id, exam_id)
    if exam["status"] not in ("running", "paused"):
        raise HTTPException(status_code=409, detail="仅未交卷的考试可放弃")
    exam["durationSec"] = _elapsed_sec(exam)
    exam["activeSince"] = None
    exam["status"] = "abandoned"
    exam["finishedAt"] = _now()
    store.persist_exam(exam)
    return {"ok": True}


@router.post("/{exam_id}/submit")
def submit(exam_id: str, user_id: str = Depends(get_current_user)) -> dict:
    """交卷判分：百分制总分 + 按 category 维度分；「只交已答」口径由前端保证。"""
    exam = _require(user_id, exam_id)
    if exam["status"] not in ("running", "paused"):
        raise HTTPException(status_code=409, detail="该考试已结束")
    if exam["status"] == "running":
        exam["durationSec"] = _elapsed_sec(exam)
    exam["activeSince"] = None
    exam["status"] = "done"
    exam["finishedAt"] = _now()

    correct = sum(1 for a in exam.get("answers", []) if a["correct"])
    exam["score"] = round(correct * 100 / exam["total"]) if exam["total"] else 0
    store.persist_exam(exam)
    # 站内信：模考报告已生成（批 4）
    store.add_notification(
        user_id, "exam_report", {"examId": exam["id"], "score": exam["score"]}
    )
    return {
        "examId": exam["id"], "score": exam["score"], "total": exam["total"],
        "answered": len(exam.get("answers", [])), "correct": correct,
        "durationSec": exam.get("durationSec", 0), "pausedSec": exam.get("pausedSec", 0),
    }


# ---------------- 报告与补强 ----------------


@router.get("/{exam_id}/report")
def report(exam_id: str, user_id: str = Depends(get_current_user)) -> dict:
    """模考报告：分数 / 维度分 / 分桶百分位 / 薄弱点 TOP5 / 历史趋势。"""
    exam = _require(user_id, exam_id)
    if exam["status"] != "done":
        raise HTTPException(status_code=409, detail="考试尚未交卷")

    by_id = {q["id"]: q for q in store.all_questions()}
    answers = exam.get("answers", [])

    # 维度分：按 category 聚合（百分制）
    dim: dict[str, dict] = {}
    for a in answers:
        q = by_id.get(a["questionId"])
        if q is None:
            continue
        slot = dim.setdefault(q.get("category") or "未分类", {"correct": 0, "total": 0})
        slot["total"] += 1
        slot["correct"] += 1 if a["correct"] else 0
    dimension_scores = [
        {"label": label, "correct": v["correct"], "total": v["total"],
         "score": round(v["correct"] * 100 / v["total"]) if v["total"] else 0}
        for label, v in dim.items()
    ]

    # 百分位：同岗位分桶 done 分数真实聚合；样本不足降级 null
    scores = store.done_scores_by_role(exam.get("bucketRole", ""))
    percentile = None
    if len(scores) >= PERCENTILE_MIN_SAMPLE and exam["score"] is not None:
        below = sum(1 for s in scores if s < exam["score"])
        percentile = round(below * 100 / len(scores))

    # 薄弱点 TOP5：本次错题按 knowledge_tags 聚合
    tag_count: dict[str, int] = {}
    for a in answers:
        if a["correct"]:
            continue
        q = by_id.get(a["questionId"])
        for tag in (q or {}).get("knowledgeTags", []):
            tag_count[tag] = tag_count.get(tag, 0) + 1
    weak_points = sorted(tag_count.items(), key=lambda kv: kv[1], reverse=True)[:5]

    history = [
        {"examId": e["id"], "score": e.get("score"), "finishedAt": e.get("finishedAt")}
        for e in store.user_exams(user_id) if e.get("status") == "done"
    ]

    correct_count = sum(1 for a in answers if a["correct"])
    return {
        "examId": exam["id"], "bucketRole": exam.get("bucketRole", ""),
        "score": exam["score"], "total": exam["total"],
        "answered": len(answers), "correct": correct_count,
        "correctRate": round(correct_count * 100 / len(answers)) if answers else 0,
        "durationSec": exam.get("durationSec", 0), "pausedSec": exam.get("pausedSec", 0),
        "finishedAt": exam.get("finishedAt"),
        "dimensionScores": dimension_scores,
        "percentile": percentile,
        "sampleSize": len(scores),
        "weakPoints": [{"tag": t, "wrongCount": c} for t, c in weak_points],
        "history": history,
    }


@router.post("/{exam_id}/remedial")
def remedial(exam_id: str, user_id: str = Depends(get_current_user)) -> dict:
    """补强练习卷：按本次薄弱知识点标签从现有题库抽同标签题（二期口径不调 LLM）。"""
    exam = _require(user_id, exam_id)
    if exam["status"] != "done":
        raise HTTPException(status_code=409, detail="考试尚未交卷")

    by_id = {q["id"]: q for q in store.all_questions()}
    tags: list[str] = []
    for a in exam.get("answers", []):
        if not a["correct"]:
            q = by_id.get(a["questionId"])
            if q:
                tags.extend(q.get("knowledgeTags", []))
    if not tags:
        detail = "本次未作答任何题目，无法定位薄弱点" if not exam.get("answers") else "本次考试全部答对，无需补强"
        raise HTTPException(status_code=400, detail=detail)

    wanted = set(tags)
    pool = [
        q for q in store.all_questions()
        if q["type"] in store.EXAM_OBJECTIVE_TYPES and wanted & set(q.get("knowledgeTags", []))
        and q["setId"] != exam["setId"]          # 优先补新题，排除本卷题集
    ] or [q for q in store.all_questions()
          if q["type"] in store.EXAM_OBJECTIVE_TYPES and wanted & set(q.get("knowledgeTags", []))]
    if not pool:
        raise HTTPException(status_code=400, detail="题库中暂无相关知识点的题目")
    random.shuffle(pool)
    picked = pool[:REMEDIAL_MAX]
    top_tag = max(wanted & {t for q in picked for t in q.get("knowledgeTags", [])}, key=lambda t: tags.count(t))

    new_set = {
        "id": store.new_id("set"),
        "source": "mock_interview",
        "title": f"补强练习 · {top_tag}",
        "questionCount": 0,
        "updatedAt": store.now_str(),
    }
    store.add_set(new_set)
    items = []
    for q in picked:
        item = dict(q)
        item["id"] = store.new_id("q")
        item["setId"] = new_set["id"]
        items.append(item)
    store.add_questions(items, new_set["id"])
    return {"setId": new_set["id"], "title": new_set["title"], "questionCount": len(items)}
