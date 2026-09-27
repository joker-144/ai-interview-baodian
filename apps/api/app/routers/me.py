"""我的 / 设置（P14 一期最小版，文档 6.2 一期范围、8.3 页面清单）。

一期落地项：个人资料、三统计卡、退出登录（前端本地会话）、注销申请（7 天冷静期）、复习提醒时间。
学习报告 / 简历管理 / 通知管理 / 数据导出随其承载功能落二期（V2.3 排期口径）。

注销是第五章合规「提供一键删除全部数据」的兑现入口，链路为：
申请（校验无进行中出题任务）→ 冷静期 7 天可撤回 → 到期异步清理 → 不可恢复。
所有端点按 JWT 识别当前用户（get_current_user）；清理由 `POST /deactivation/execute`
手动触发以验证链路，接 PG 后改为定时任务扫描 `deactivation_cooling_until` 到期项执行。
"""

import math
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from app import store
from app.auth import get_current_user
from app.schemas import (
    DeactivateRequest,
    DeactivationInfo,
    DeletionResult,
    MeProfile,
    ProfileUpdate,
    UserSettings,
    UserSettingsUpdate,
)

router = APIRouter(prefix="/api/me", tags=["me"])


def _now() -> datetime:
    return datetime.now()


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _remaining_days(until: datetime) -> int:
    """冷静期剩余天数：不足一天按一天计，已到期为 0。"""
    seconds = (until - _now()).total_seconds()
    return max(0, math.ceil(seconds / 86400))


def _deactivation_info(user_id: str) -> DeactivationInfo | None:
    deact = store.user_deactivation(user_id)
    if not deact:
        return None
    until = datetime.strptime(deact["coolingOffUntil"], "%Y-%m-%d %H:%M:%S")
    executed = deact["status"] == "executed"
    return DeactivationInfo(
        status=deact["status"],
        requestedAt=deact["requestedAt"],
        coolingOffUntil=deact["coolingOffUntil"],
        remainingDays=0 if executed else _remaining_days(until),
        reason=deact.get("reason", ""),
        scopes=deact.get("scopes", []),
        # 已执行 = 数据已物理清理，不可撤回
        revocable=not executed,
    )


def _wrong_items(user_id: str) -> list[dict]:
    return store.state.wrong_book.get(user_id, [])


def _assert_active(user_id: str) -> None:
    """注销已执行的账号不再接受资料与偏好变更（真实实现中该账号已不可登录）。"""
    deact = store.user_deactivation(user_id)
    if deact and deact["status"] == "executed":
        raise HTTPException(status_code=410, detail="账号已注销，数据不可恢复，无法修改资料或设置")


def _build(user_id: str) -> MeProfile:
    """资料 = USER 种子被该用户覆盖项叠加；统计卡与计数按实时数据聚合。"""
    merged = store.user_profile(user_id)
    pending = sum(1 for w in _wrong_items(user_id) if not w["mastered"])
    mastered = store.MASTERED_BASE + sum(1 for w in _wrong_items(user_id) if w["mastered"])
    return MeProfile(
        profile={k: merged[k] for k in (
            "name", "avatarText", "targetRole", "years",
            "streak", "totalAnswered", "correctRate", "phone", "wechatBound",
        )},
        settings=UserSettings(**store.user_settings(user_id)),
        stats={
            "answered": merged["totalAnswered"],
            "correctRate": merged["correctRate"],
            "streak": merged["streak"],
            "pendingReview": pending,
            "mastered": mastered,
        },
        counts={
            "sets": len(store.state.sets),
            "favorites": len(store.state.favorites.get(user_id, [])),
            "questions": len(store.all_questions()),
        },
        deactivation=_deactivation_info(user_id),
    )


@router.get("", response_model=MeProfile)
def get_me(user_id: str = Depends(get_current_user)) -> MeProfile:
    return _build(user_id)


@router.put("", response_model=MeProfile)
def update_profile(body: ProfileUpdate, user_id: str = Depends(get_current_user)) -> MeProfile:
    _assert_active(user_id)
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    if not changes:
        raise HTTPException(status_code=400, detail="没有需要更新的资料项")
    # 改了姓名但未单独指定头像字时，头像跟随姓名首字（避免头像与姓名长期不一致）
    if "name" in changes and "avatarText" not in changes:
        changes["avatarText"] = changes["name"].strip()[0]
    store.state.profiles[user_id].update(changes)
    store.persist_user(user_id)
    return _build(user_id)


@router.put("/settings", response_model=MeProfile)
def update_settings(body: UserSettingsUpdate, user_id: str = Depends(get_current_user)) -> MeProfile:
    _assert_active(user_id)
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    if not changes:
        raise HTTPException(status_code=400, detail="没有需要更新的设置项")
    store.state.settings_map[user_id].update(changes)
    store.persist_user(user_id)
    return _build(user_id)


@router.delete("", response_model=DeactivationInfo)
def request_deactivation(
    body: DeactivateRequest | None = None, user_id: str = Depends(get_current_user)
) -> DeactivationInfo:
    """注销申请：进入 7 天冷静期，期间可撤回，到期执行不可逆清理。"""
    current = store.user_deactivation(user_id)
    if current and current["status"] == "executed":
        raise HTTPException(status_code=409, detail="账号已注销，数据不可恢复")
    if current:
        # 重复申请不重置冷静期，避免靠反复申请拖延清理
        raise HTTPException(status_code=409, detail=f"已有进行中的注销申请，冷静期至 {current['coolingOffUntil']}")

    running = [t.task_id for t in store.state.generate_tasks.values() if not t.done]
    if running:
        raise HTTPException(
            status_code=409,
            detail=f"存在进行中的出题任务（{len(running)} 个），请等待完成或取消后再申请注销",
        )

    now = _now()
    until = now + timedelta(days=store.DEACTIVATION_COOLING_DAYS)
    store.state.deactivations[user_id] = {
        "status": "cooling_off",
        "requestedAt": _fmt(now),
        "coolingOffUntil": _fmt(until),
        "reason": (body.reason.strip() if body and body.reason else ""),
        "scopes": list(store.DELETION_SCOPES),
    }
    info = _deactivation_info(user_id)
    assert info is not None
    store.persist_user(user_id)
    return info


@router.post("/deactivation/cancel", response_model=MeProfile)
def cancel_deactivation(user_id: str = Depends(get_current_user)) -> MeProfile:
    """冷静期内撤回注销：清空申请，数据保持原样。"""
    current = store.user_deactivation(user_id)
    if not current:
        raise HTTPException(status_code=404, detail="当前没有进行中的注销申请")
    if current["status"] == "executed":
        raise HTTPException(status_code=409, detail="注销已执行，数据已清理，无法撤回")
    store.state.deactivations[user_id] = None
    store.persist_user(user_id)
    return _build(user_id)


@router.post("/deactivation/execute", response_model=DeletionResult)
def execute_deactivation(
    force: bool = Query(default=False), user_id: str = Depends(get_current_user)
) -> DeletionResult:
    """执行数据清理（真实系统由定时任务在冷静期到期后触发）。

    一期无定时任务，`force=true` 用于跳过到期校验以验证「申请 → 冷静期 → 清理」链路。
    """
    current = store.user_deactivation(user_id)
    if not current:
        raise HTTPException(status_code=404, detail="当前没有进行中的注销申请")
    if current["status"] == "executed":
        raise HTTPException(status_code=409, detail="清理已执行，请勿重复调用")

    until = datetime.strptime(current["coolingOffUntil"], "%Y-%m-%d %H:%M:%S")
    remaining = _remaining_days(until)
    if remaining > 0 and not force:
        raise HTTPException(
            status_code=409,
            detail=f"仍在冷静期内（剩余 {remaining} 天，至 {current['coolingOffUntil']}）；"
                   f"该期间可撤回，如需验证清理链路请加 force=true",
        )

    st = store.state
    resume = st.resumes.get(user_id)
    deleted = {
        "progress": len(st.progress.pop(user_id, {})),
        "wrongBook": len(st.wrong_book.pop(user_id, [])),
        "favorites": len(st.favorites.pop(user_id, [])),
        "questionSets": len(st.sets),
        "plans": len(st.plans),
        # 简历原文件在对象存储，此处对应物理删除（清内存解析产物引用）
        "resume": 1 if resume else 0,
    }
    st.sets.clear()
    st.plans.clear()
    st.resumes[user_id] = None
    # 账号标记为已注销：统计归零，资料不再可读
    st.profiles[user_id].update({
        "name": "已注销用户",
        "avatarText": "注",
        "targetRole": "—",
        "years": 0,
        "streak": 0,
        "totalAnswered": 0,
        "correctRate": 0,
        "phone": "",
        "wechatBound": False,
    })
    current["status"] = "executed"
    current["executedAt"] = _fmt(_now())

    # 物理清理：用户的进度/错题/收藏/简历产物 + 全站题库；users 行保留注销标记
    store.purge_user_data(user_id)
    store.persist_user(user_id)

    return DeletionResult(
        ok=True,
        executedAt=current["executedAt"],
        deleted=deleted,
        detail=f"已清理 {sum(deleted.values())} 项数据（含简历原文件物理删除），账号不可恢复",
    )
