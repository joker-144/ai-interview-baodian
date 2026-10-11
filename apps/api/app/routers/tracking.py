"""岗位追踪订阅管理 API（七期 Phase 1，文档 6.2 七期）。

- 订阅 CRUD：关键词 + 城市 + 高分推送阈值（minScore，默认 75，用户可配）；
  上限 store.MAX_SUBSCRIPTIONS_PER_USER（检索量与通知量保护），同关键词+城市去重；
- POST /run：手动触发当前用户一轮追踪（与 Celery beat 每日定时跑的是同一套
  app.tracking.run_user_tracking 管线；seenIds 幂等保证不重复推送）——
  未部署 Redis/worker 时的演示与联调入口，也是「立即跑一次」的产品能力。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import store, tracking
from app.auth import get_current_user

router = APIRouter(prefix="/api/tracking", tags=["tracking"])


class SubscriptionCreate(BaseModel):
    keyword: str = Field(min_length=1, max_length=64)
    city: str = Field(default="", max_length=32)
    minScore: int = Field(default=75, ge=0, le=100)


class SubscriptionPatch(BaseModel):
    enabled: bool | None = None
    minScore: int | None = Field(default=None, ge=0, le=100)


def _sub_payload(sub: dict) -> dict:
    """订阅出参：附带追踪状态（上次运行时刻 / 运行摘要，前端展示用）。"""
    state = store.tracking_state_get(sub["id"], sub["userId"])
    return {
        **sub,
        "lastRunAt": state.get("lastRunAt") or "",
        "lastResult": state.get("lastResult"),
    }


@router.get("/subscriptions")
def list_subscriptions(user_id: str = Depends(get_current_user)) -> dict:
    """我的订阅列表（含停用；按创建时间升序）。"""
    store.ensure_user(user_id)
    return {"items": [_sub_payload(s) for s in store.subscriptions_of(user_id)]}


@router.post("/subscriptions", status_code=201)
def create_subscription(
    body: SubscriptionCreate, user_id: str = Depends(get_current_user)
) -> dict:
    """新增订阅：同关键词+城市视为重复（409）；超过上限拒绝（409）。"""
    store.ensure_user(user_id)
    keyword = body.keyword.strip()
    city = body.city.strip()
    existing = store.subscriptions_of(user_id)
    if any(s["keyword"] == keyword and s["city"] == city for s in existing):
        raise HTTPException(status_code=409, detail="已订阅相同的关键词与城市，请勿重复订阅")
    if len(existing) >= store.MAX_SUBSCRIPTIONS_PER_USER:
        raise HTTPException(
            status_code=409,
            detail=f"订阅数量已达上限 {store.MAX_SUBSCRIPTIONS_PER_USER} 个，请先删除不需要的订阅",
        )
    sub = store.subscription_add(user_id, keyword=keyword, city=city, min_score=body.minScore)
    return _sub_payload(sub)


@router.put("/subscriptions/{sub_id}")
def update_subscription(
    sub_id: str, body: SubscriptionPatch, user_id: str = Depends(get_current_user)
) -> dict:
    """更新订阅（启停 / 阈值）。"""
    store.ensure_user(user_id)
    sub = store.subscription_update(
        user_id, sub_id, enabled=body.enabled, min_score=body.minScore
    )
    if sub is None:
        raise HTTPException(status_code=404, detail="订阅不存在")
    return _sub_payload(sub)


@router.delete("/subscriptions/{sub_id}")
def delete_subscription(sub_id: str, user_id: str = Depends(get_current_user)) -> dict:
    """删除订阅（连带追踪基线）。"""
    store.ensure_user(user_id)
    if not store.subscription_remove(user_id, sub_id):
        raise HTTPException(status_code=404, detail="订阅不存在")
    return {"ok": True}


@router.post("/run")
async def run_tracking_now(user_id: str = Depends(get_current_user)) -> dict:
    """手动触发一轮岗位追踪（当前用户；跑 Celery beat 同款管线）。

    同步返回运行轨迹汇总；Boss 未登录 / 风控等场景静默中止（aborted 非空），
    不产生任何通知。注意：真实检索含 CLI 内置节流，单订阅可能耗时数十秒。
    """
    store.ensure_user(user_id)
    return await tracking.run_user_tracking(user_id, trigger="manual")
