"""求职看板（四期，文档 P4 / job_pipeline 四列状态机）。

借鉴 BossHunter 工作台设计（状态跟进 / 趋势统计 / 回收站 / 平台能力边界降级），
但严格保持只读合规：本路由是纯本地状态机，不触达任何招聘平台、不自动投递、不打招呼、
不监听 HR——状态流转与 HR 回复备注均由用户手动维护。

岗位来源两种：
- zhipin 卡（带 securityId）：加入时从检索缓存回填岗位信息 + 两阶段漏斗匹配分，
  并按关键词挂接该「岗位市场题集」，卡片显示练习进度（融合引擎 B）；
- 外部平台 / 手动卡：用户手动录入（智联 / 51job / 猎聘 / 其他），无自动回填——能力边界降级。

面试日期联动：stage=interview 且 interview_at 在未来 ≤3 天时，GET 惰性补一条
interview_prep 复习提醒（当日去重，复用 notifications 机制，无定时器口径）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import store
from app.auth import get_current_user

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])

STAGES = store.PIPELINE_STAGES  # applied / written / interview / offer
PLATFORMS = {"zhipin", "zhilian", "51job", "liepin", "manual"}
# 面试临近提醒窗口（天）
INTERVIEW_REMIND_DAYS = 3


class AddCardRequest(BaseModel):
    securityId: str = Field(default="", max_length=512)
    keyword: str = Field(default="", max_length=128)
    city: str = Field(default="", max_length=64)
    jobName: str = Field(default="", max_length=255)
    brand: str = Field(default="", max_length=255)
    salary: str = Field(default="", max_length=64)
    platform: str = Field(default="", max_length=16)
    note: str = Field(default="", max_length=2000)
    stage: str = Field(default="applied", max_length=16)
    interviewAt: str = Field(default="", max_length=32)


class PatchCardRequest(BaseModel):
    stage: str | None = Field(default=None, max_length=16)
    note: str | None = Field(default=None, max_length=2000)
    interviewAt: str | None = Field(default=None, max_length=32)
    setId: str | None = Field(default=None, max_length=64)


def _card_payload(user_id: str, card: dict[str, Any]) -> dict[str, Any]:
    """看板卡出参：附带挂接题集的练习进度（避免前端逐卡拉取）。"""
    progress = store.set_practice_progress(user_id, card.get("setId", ""))
    return {**card, "practice": progress}


def _resolve_keyword_set(user_id: str, keyword: str) -> str:
    """按关键词解析该「岗位市场题集」id（复用 question_sets 归并口径）；无则空串。"""
    title = f"{keyword or '岗位市场'} · 岗位市场题集"
    target = next(
        (
            s
            for s in store.visible_sets(user_id)
            if s.get("source") == "job_search" and s.get("title") == title
        ),
        None,
    )
    return target["id"] if target else ""


def _lookup(keyword: str, city: str, security_id: str) -> tuple[dict[str, Any] | None, int | None]:
    """按单个缓存键（sha256(关键词|城市)）查岗位卡与两阶段漏斗匹配分。"""
    map_key = store.jobs_cache_key(keyword, city)
    job = None
    cached = store.jobs_cache_get(map_key)
    for item in (cached or {}).get("payload") or []:
        if item.get("securityId") == security_id:
            job = item
            break
    agg = store.job_map_get(map_key) or {}
    entry = (agg.get("jobScores") or {}).get(security_id)
    score = None
    if isinstance(entry, dict) and entry.get("score") is not None:
        try:
            score = int(entry["score"])
        except (TypeError, ValueError):
            score = None
    return job, score


def _fill_from_cache(keyword: str, city: str, security_id: str) -> tuple[dict[str, Any] | None, int | None]:
    """从检索缓存 / 考点地图回填岗位卡与两阶段漏斗匹配分。

    缓存键为 sha256(查询关键词|查询城市)。若调用方误传岗位自身城市（查询城市为空时
    前端易回退成 job.city），精确键未命中则按空城市键回退再查一次，避免回填失败。
    """
    job, score = _lookup(keyword, city, security_id)
    if job is None and city:
        job, score = _lookup(keyword, "", security_id)
    return job, score


def _stats(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """趋势统计：各阶段计数 / 本周新增 / 待面试 / 平均匹配分。"""
    today = datetime.now().date()
    week_start = today - timedelta(days=today.weekday())
    by_stage = {s: 0 for s in STAGES}
    new_this_week = 0
    pending_interview = 0
    score_sum = 0
    score_n = 0
    for card in cards:
        stage = card["stage"] if card["stage"] in by_stage else "applied"
        by_stage[stage] += 1
        created = (card.get("createdAt") or "")[:10]
        try:
            if created and datetime.strptime(created, "%Y-%m-%d").date() >= week_start:
                new_this_week += 1
        except ValueError:
            pass
        if stage == "interview" and card.get("interviewAt"):
            pending_interview += 1
        if card.get("matchScore") is not None:
            score_sum += int(card["matchScore"])
            score_n += 1
    return {
        "total": len(cards),
        "byStage": by_stage,
        "newThisWeek": new_this_week,
        "pendingInterview": pending_interview,
        "avgMatchScore": round(score_sum / score_n) if score_n else None,
    }


def _lazy_interview_prep(user_id: str) -> None:
    """面试临近提醒：未来 ≤N 天有面试且当日未写过时补一条 interview_prep（当日只写一次）。"""
    today = datetime.now().date()
    today_key = today.strftime("%Y-%m-%d")
    if store.find_notification_by_date(user_id, "interview_prep", today_key):
        return
    horizon = today + timedelta(days=INTERVIEW_REMIND_DAYS)
    upcoming: list[tuple[Any, dict[str, Any]]] = []
    for card in store.pipeline_cards(user_id):
        if card.get("stage") != "interview" or not card.get("interviewAt"):
            continue
        try:
            day = datetime.strptime(card["interviewAt"][:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if today <= day <= horizon:
            upcoming.append((day, card))
    if not upcoming:
        return
    upcoming.sort(key=lambda pair: pair[0])
    nearest = upcoming[0][1]
    store.add_notification(
        user_id,
        "interview_prep",
        {
            "jobName": nearest.get("jobName", ""),
            "interviewAt": nearest["interviewAt"][:10],
            "setId": nearest.get("setId", ""),
            "cardId": nearest["id"],
            "count": len(upcoming),
        },
    )


@router.get("")
def board(user_id: str = Depends(get_current_user)) -> dict[str, Any]:
    """四列看板（排除回收站）+ 趋势统计；顺带惰性触发面试临近提醒。"""
    store.ensure_user(user_id)
    _lazy_interview_prep(user_id)
    cards = store.pipeline_cards(user_id)
    columns: dict[str, list[dict[str, Any]]] = {s: [] for s in STAGES}
    for card in cards:
        stage = card["stage"] if card["stage"] in columns else "applied"
        columns[stage].append(_card_payload(user_id, card))
    return {"columns": columns, "stats": _stats(cards), "stages": STAGES}


@router.get("/trash")
def trash(user_id: str = Depends(get_current_user)) -> dict[str, Any]:
    """回收站（软删的看板卡）。"""
    store.ensure_user(user_id)
    cards = [c for c in store.pipeline_cards(user_id, include_trash=True) if c.get("inTrash")]
    return {"cards": [_card_payload(user_id, c) for c in cards]}


@router.post("")
def add_card(body: AddCardRequest, user_id: str = Depends(get_current_user)) -> dict[str, Any]:
    """加入看板：zhipin 卡从检索缓存回填 + 匹配分 + 挂题集；外部平台/手动卡直接录入。"""
    store.ensure_user(user_id)
    security_id = body.securityId.strip()
    keyword = body.keyword.strip()
    city = body.city.strip()
    stage = body.stage if body.stage in STAGES else "applied"
    job_name = body.jobName.strip()
    brand = body.brand.strip()
    salary = body.salary.strip()
    match_score: int | None = None
    set_id = ""

    if security_id:
        platform = body.platform if body.platform in PLATFORMS else "zhipin"
        job, match_score = _fill_from_cache(keyword, city, security_id)
        if job:
            job_name = job_name or str(job.get("jobName", ""))
            brand = brand or str(job.get("brand", ""))
            salary = salary or str(job.get("salary", ""))
            city = city or str(job.get("city", ""))
            keyword = keyword or str(job.get("keyword", ""))
        set_id = _resolve_keyword_set(user_id, keyword)
        # 去重：同一岗位已在看板（非回收站）则直接复用，不重复加卡
        existed = next(
            (c for c in store.pipeline_cards(user_id) if c.get("securityId") == security_id),
            None,
        )
        if existed:
            return _card_payload(user_id, existed)
    else:
        platform = body.platform if body.platform in PLATFORMS else "manual"

    if not job_name:
        if security_id:
            raise HTTPException(
                status_code=422,
                detail="检索缓存中未找到该岗位（缓存可能已过期或后端重启）：请重新检索后再加入，或在求职看板手动录入",
            )
        raise HTTPException(status_code=422, detail="岗位名不能为空")

    card = {
        "id": store.new_id("pipe"),
        "userId": user_id,
        "stage": stage,
        "securityId": security_id,
        "keyword": keyword,
        "jobName": job_name,
        "brand": brand,
        "city": city,
        "salary": salary,
        "platform": platform,
        "matchScore": match_score,
        "setId": set_id,
        "interviewAt": body.interviewAt.strip(),
        "note": body.note.strip(),
        "inTrash": False,
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    store.pipeline_add(user_id, card)
    return _card_payload(user_id, card)


@router.patch("/{card_id}")
def patch_card(
    card_id: str, body: PatchCardRequest, user_id: str = Depends(get_current_user)
) -> dict[str, Any]:
    """流转阶段 / 改备注 / 设面试日期 / 换挂题集（仅本人卡，否则 404）。"""
    card = store.pipeline_find(user_id, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="看板卡不存在")
    if body.stage is not None:
        if body.stage not in STAGES:
            raise HTTPException(status_code=422, detail="非法阶段")
        card["stage"] = body.stage
    if body.note is not None:
        card["note"] = body.note.strip()
    if body.interviewAt is not None:
        card["interviewAt"] = body.interviewAt.strip()
    if body.setId is not None:
        set_id = body.setId.strip()
        if set_id and not store.can_access_set(set_id, user_id):
            raise HTTPException(status_code=404, detail="题集不存在")
        card["setId"] = set_id
    store.pipeline_save(user_id, card)
    return _card_payload(user_id, card)


@router.delete("/{card_id}")
def soft_delete(card_id: str, user_id: str = Depends(get_current_user)) -> dict[str, Any]:
    """移入回收站（软删）。"""
    card = store.pipeline_find(user_id, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="看板卡不存在")
    card["inTrash"] = True
    store.pipeline_save(user_id, card)
    return {"ok": True}


@router.post("/{card_id}/restore")
def restore(card_id: str, user_id: str = Depends(get_current_user)) -> dict[str, Any]:
    """从回收站还原。"""
    card = store.pipeline_find(user_id, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="看板卡不存在")
    card["inTrash"] = False
    store.pipeline_save(user_id, card)
    return _card_payload(user_id, card)


@router.delete("/{card_id}/permanent")
def purge(card_id: str, user_id: str = Depends(get_current_user)) -> dict[str, Any]:
    """彻底删除（回收站永久删除）。"""
    if not store.pipeline_purge(user_id, card_id):
        raise HTTPException(status_code=404, detail="看板卡不存在")
    return {"ok": True}
