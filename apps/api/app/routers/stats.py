"""轻量统计：本周刷题柱状图与汇总（一期 Mock；二期由 Redis 计数聚合）。"""

from fastapi import APIRouter

from app import store

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/week")
def week_stats() -> dict:
    return {"bars": store.WEEK_BARS, "stats": store.WEEK_STATS}
