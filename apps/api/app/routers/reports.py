"""学习周报（二期，文档 3.11 / 4.5 GET /api/reports/weekly）。

从真实作答事件与错题本聚合近 7 天趋势与薄弱知识点；「下周建议」由轻量模型
生成并按「周-用户」缓存（每周仅调一次，LLM 失败降级为规则模板文案）。
"""

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends

from app import llm, store
from app.auth import get_current_user

router = APIRouter(prefix="/api/reports", tags=["reports"])

# weekKey -> suggestion 文案（进程级缓存；重启后当周首次请求会重新生成）
_suggestion_cache: dict[str, str] = {}


def _day_labels(events: list[dict], days: int = 7) -> list[dict]:
    """近 N 天逐日趋势（补齐缺天），与 /api/stats/week 同口径。"""
    today = datetime.now().date()
    buckets: dict[str, dict] = {}
    for offset in range(days - 1, -1, -1):
        day = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        buckets[day] = {"date": day, "answered": 0, "correct": 0}
    for ev in events:
        if ev["date"] in buckets:
            buckets[ev["date"]]["answered"] += int(ev["answered"])
            buckets[ev["date"]]["correct"] += int(ev["correct"])
    return [
        {
            "date": day,
            "answered": item["answered"],
            "correctRate": round(item["correct"] * 100 / item["answered"]) if item["answered"] else None,
        }
        for day, item in sorted(buckets.items())
    ]


def _weak_points(user_id: str, top: int = 5) -> list[dict]:
    """薄弱知识点 TOP5：未掌握错题按题目 knowledge_tags 聚合。"""
    tag_count: dict[str, int] = {}
    for w in store.state.wrong_book.get(user_id, []):
        if w["mastered"]:
            continue
        question = store.find_question(w["questionId"])
        for tag in (question or {}).get("knowledgeTags", []):
            tag_count[tag] = tag_count.get(tag, 0) + 1
    ranked = sorted(tag_count.items(), key=lambda kv: kv[1], reverse=True)[:top]
    return [{"tag": tag, "wrongCount": count} for tag, count in ranked]


def _rule_suggestion(weak: list[dict], answered: int, correct_rate: int) -> str:
    if not weak:
        if answered > 0:
            return "本周保持稳定作答，下周可开启新一轮专项冲刺，并关注模考成绩变化。"
        return "本周作答较少，建议先从进行中的题集继续，积累 50 题以上再复盘。"
    names = "、".join(w["tag"] for w in weak[:3])
    tone = "保持节奏" if correct_rate >= 70 else "优先巩固基础"
    return f"薄弱点集中在 {names}，下周建议针对性补强（{tone}），完成错题复习后用补强练习卷验证。"


@router.get("/weekly")
def weekly_report(user_id: str = Depends(get_current_user)) -> dict:
    store.ensure_user(user_id)

    events = store.user_week_events(user_id, days=7)
    trend = _day_labels(events)
    answered = sum(t["answered"] for t in trend)
    correct = sum(
        round(t["answered"] * t["correctRate"] / 100)
        for t in trend if t["correctRate"] is not None
    )
    correct_rate = round(correct * 100 / answered) if answered else 0
    weak = _weak_points(user_id)
    mastered_total = store.MASTERED_BASE + sum(
        1 for w in store.state.wrong_book.get(user_id, []) if w["mastered"]
    )

    # 「下周建议」：每周一条，轻量模型生成（失败降级规则模板）
    week_key = datetime.now().strftime("%G-W%V") + ":" + user_id
    suggestion = _suggestion_cache.get(week_key)
    if not suggestion:
        suggestion = _rule_suggestion(weak, answered, correct_rate)
        try:
            data = llm.chat_sync("light", [
                {"role": "system", "content": (
                    "你是面试训练助教。根据用户本周学习数据输出一句下周学习建议，"
                    "60 字以内，具体可执行，不要客套。"
                )},
                {"role": "user", "content": json.dumps({
                    "targetRole": store.user_profile(user_id).get("targetRole", ""),
                    "weekAnswered": answered,
                    "weekCorrectRate": correct_rate,
                    "weakPoints": [w["tag"] for w in weak],
                    "masteredTotal": mastered_total,
                }, ensure_ascii=False)},
            ], temperature=0.4, max_tokens=200, thinking=False)
            text = llm.strip_fence(data).strip().strip('"')
            if 10 <= len(text) <= 120:
                suggestion = text
        except Exception:
            pass  # LLM 不可用时保留规则模板
        _suggestion_cache[week_key] = suggestion

    return {
        "weekKey": week_key.split(":")[0],
        "trend": trend,
        "answered": answered,
        "correctRate": correct_rate,
        "masteredTotal": mastered_total,
        "weakPoints": weak,
        "suggestion": suggestion,
    }
