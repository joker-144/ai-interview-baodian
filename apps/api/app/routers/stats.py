"""轻量统计（二期）：本周柱状图与三卡，从真实作答事件聚合。

数据源：MySQL 模式直查 answer_events（store.user_week_events 内部封装）；
内存模式用 store.answer_log 留底聚合。柱状图固定回传近 7 天（无作答日为 0）。
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends

from app import store
from app.auth import get_current_user

router = APIRouter(prefix="/api/stats", tags=["stats"])

_DAY_LABELS = ["一", "二", "三", "四", "五", "六", "日"]


def _week_buckets(events: list[dict], days: int = 7) -> dict[str, dict]:
    """把逐日事件（可能缺天）补齐成固定 N 天的桶，按日期升序。"""
    today = datetime.now().date()
    buckets: dict[str, dict] = {}
    for offset in range(days - 1, -1, -1):
        day = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        buckets[day] = {"date": day, "answered": 0, "correct": 0}
    for ev in events:
        if ev["date"] in buckets:
            buckets[ev["date"]]["answered"] += int(ev["answered"])
            buckets[ev["date"]]["correct"] += int(ev["correct"])
    return buckets


@router.get("/week")
def week_stats(user_id: str = Depends(get_current_user)) -> dict:
    store.ensure_user(user_id)

    # 近 7 天逐日作答（MySQL answer_events / 内存 answer_log）
    raw = store.user_week_events(user_id, days=7)
    buckets = _week_buckets(raw)

    bars = []
    total_answered = 0
    total_correct = 0
    for day, item in buckets.items():
        label = _DAY_LABELS[datetime.strptime(day, "%Y-%m-%d").weekday()]
        bars.append({"day": label, "value": item["answered"], "date": day})
        total_answered += item["answered"]
        total_correct += item["correct"]

    pending_review = sum(
        1 for w in store.state.wrong_book.get(user_id, []) if not w["mastered"]
    )
    return {
        "bars": bars,
        "stats": {
            "answered": total_answered,
            "correctRate": round(total_correct * 100 / total_answered) if total_answered else 0,
            "pendingReview": pending_review,
        },
    }
