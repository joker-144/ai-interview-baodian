"""Celery 实例与 beat 调度（七期岗位追踪，文档 6.2 七期 / 6.5 第 8 条）。

只有 worker 进程加载本模块——API 进程（main.py）不 import 这里，
未安装 celery / 未部署 Redis 时 API 完全不受影响，仅每日定时链路不可用
（手动触发接口 POST /api/tracking/run 仍可在 API 进程内跑同一套管线）。

启动方式（Windows 下 prefork 不可用，必须 --pool=solo）：
    celery -A app.celery_app worker --beat --pool=solo --loglevel=info

worker 与 API 是两个进程，订阅 / diff 基线 / 通知经 MySQL 共享：
worker 侧要求 MySQL 可用（纯内存模式下定时链路自然空转，见 app.tracking）。
"""

from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "ai-interview-baodian",
    broker=settings.celery_broker_url,
    include=["app.tasks"],
)

# 时区与业务对齐（每日 1 次的触发时刻以本地时区计算）
celery_app.conf.timezone = "Asia/Shanghai"
celery_app.conf.enable_utc = False

# 每日岗位追踪：TRACKING_HOUR 点整触发 1 次（V2.12 确认「每日 1 次」）
celery_app.conf.beat_schedule = {
    "daily-job-tracking": {
        "task": "app.tasks.daily_job_tracking",
        "schedule": crontab(hour=settings.tracking_hour, minute=0),
    },
}
