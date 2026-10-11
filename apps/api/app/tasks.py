"""Celery 定时任务（七期岗位追踪）：仅 worker 进程加载本模块。

daily_job_tracking 由 beat 按 TRACKING_HOUR 每日触发 1 次（见 app.celery_app），
逐用户跑 app.tracking.run_user_tracking 同一套管线；
单用户失败不影响其余用户（兜底捕获，摘要里记录错误）。

API 进程不 import 本模块（routers/tracking.py 的手动触发直接调 app.tracking，
不经 Celery），因此未安装 celery/redis 不影响 API 启动。
"""

from __future__ import annotations

import asyncio
import logging

from app.celery_app import celery_app
from app import tracking

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.daily_job_tracking")
def daily_job_tracking() -> dict:
    """每日一次：全量启用订阅用户各跑一轮岗位追踪（检索 diff 推送 + 下架探测）。

    幂等性由 seenIds diff 基线保证：worker 重启 / beat 补跑同日不会重复推送。
    """
    users = tracking.all_subscribed_users()
    summary: dict = {"trigger": "beat", "users": 0, "runs": []}
    for user_id in users:
        summary["users"] += 1
        try:
            # run_user_tracking 是 async 管线（LLM 评分走 async 客户端），worker 内同步包装
            result = asyncio.run(tracking.run_user_tracking(user_id, trigger="beat"))
        except Exception as exc:  # noqa: BLE001 —— 单用户失败静默，不影响其余用户
            logger.warning("岗位追踪单用户运行失败：%s %s", user_id, exc)
            result = {"name": f"job-tracking:{user_id}", "error": f"{type(exc).__name__}: {exc}"}
        summary["runs"].append(result)
    logger.info("每日岗位追踪完成：%s", {k: v for k, v in summary.items() if k != "runs"})
    return summary
