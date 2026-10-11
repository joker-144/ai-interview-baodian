"""岗位追踪自主（七期 Phase 1，文档 6.2 七期）。

每日一次的 agentic 管线（Celery beat 触发，亦可供 API 手动触发复用同一链路）：
订阅读取 → 只读检索（boss search，复用分层采样与检索缓存）→ diff 新岗位
→ 两阶段匹配分过滤（复用引擎 B 漏斗）→ 站内通知（高分逐条 / 低分汇总）
→ 看板 zhipin 卡按日轮转下架探测（连续 miss 才判定下架，防单次抖动误报）。

合规与降级口径（七期硬约束）：
- 只读合规面集中在 TOOLS（status / search / detail 三个原语），不触碰投递、打招呼等动作；
- 未装 CLI / 未登录 / 风控 / 专用浏览器未就绪 → 整轮静默中止，不产生任何通知；
- 单订阅超时 / 上游异常 → 该订阅记失败步骤后继续其余订阅，不中断整轮；
- 幂等：seenIds diff 基线保证同日重复运行不重复推送；
- 全部步骤进入 AgentRun 轨迹，随返回值导出（可观测性，文档 6.2 测试重点）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app import agent_runtime, boss_cli, db, generation, llm, store
from app.agent_runtime import AgentRun, ToolRegistry
from app.config import settings
from app.routers import jobs as jobs_router

# 只读合规面：boss-agent-cli 三个只读原语集中登记于此（不登记任何动作类命令）
TOOLS = ToolRegistry()
TOOLS.register("status", boss_cli.boss_status)
TOOLS.register("search", boss_cli.search_jobs)
TOOLS.register("detail", boss_cli.job_detail)

# 新岗位进入 Stage2 LLM 评分的上限（与出题链路 PREFILTER_KEEP 同口径）
SCORE_KEEP = generation.PREFILTER_KEEP
# 单订阅单日高分逐条推送上限（防轰炸；溢出部分静默入基线，不逐条也不汇总打扰）
HIGH_PUSH_LIMIT = 5
# 下架判定阈值：跨日连续 miss 次数（两次详情失效才判定，防单次网络抖动误报）
OFFLINE_MISS_LIMIT = 2
# 高分推送默认阈值（订阅未配置时兜底；正常值来自订阅行 minScore）
DEFAULT_MIN_SCORE = 75

# 整轮中止的错误类：环境不可用（未装 CLI / 未登录 / 风控 / 专用浏览器未就绪），
# 继续跑只会逐订阅反复失败，静默中止整轮（BossTimeoutError / BossUpstreamError 只跳过单订阅）
_ABORT_ERRORS = (
    boss_cli.BossMissingError,
    boss_cli.BossAuthError,
    boss_cli.BossRiskError,
    boss_cli.BossBrowserError,
)


def all_subscribed_users() -> list[str]:
    """定时 worker 侧：全部启用订阅的归属用户（db 全量读取；纯内存模式下
    worker 与 API 是两个进程、拿不到 API 内存，此时定时链路自然空转——
    手动触发接口仍可在 API 进程内跑同一套管线，演示/联调不受影响）。"""
    rows = db.load_all_subscriptions()
    if not rows:
        return []
    return sorted({r["userId"] for r in rows if r.get("enabled")})


def _preflight(run: AgentRun) -> str:
    """运行前置检：CLI 安装态 + Boss 登录态；不过则整轮静默中止（不产生通知）。"""
    status = TOOLS.call("status")
    if not status.get("cliInstalled"):
        run.abort("boss-agent-cli 未安装")
        return "CLI 未安装，本轮跳过"
    if not status.get("loggedIn"):
        run.abort("Boss 登录态缺失")
        return "未登录，本轮跳过"
    return "登录态就绪"


async def _score_new_jobs(
    user_id: str, keyword: str, new_jobs: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], int, str]]:
    """两阶段匹配分（复用引擎 B 漏斗）：词法预筛 top-K → light 层批量评分。

    LLM 不可用时降级为 Stage1 词法分（与出题链路同口径），保证推送链路不因评分失败而断。
    返回 [(岗位卡, 匹配分 0~100, 一句话理由)]。
    """
    criteria = generation._criteria_of(user_id, keyword)
    prefetched = generation._prefilter_jobs(new_jobs, criteria, SCORE_KEEP)
    verdicts: dict[str, dict[str, Any]] = {}
    try:
        verdicts = await generation._score_jobs_llm([job for job, _ in prefetched], criteria)
    except llm.LlmError:
        verdicts = {}  # 降级词法分：verdicts 缺项时逐岗位回退预筛分
    scored: list[tuple[dict[str, Any], int, str]] = []
    for job, lexical in prefetched:
        verdict = verdicts.get(job["securityId"]) or {}
        try:
            score = int(verdict.get("score"))
        except (TypeError, ValueError):
            score = lexical
        scored.append((job, max(0, min(100, score)), str(verdict.get("reason") or "").strip()[:60]))
    return scored


async def _track_subscription(run: AgentRun, user_id: str, sub: dict[str, Any]) -> str:
    """单个订阅的一轮追踪：检索 → diff → 评分过滤 → 推送 → 基线更新。"""
    try:
        keyword = sub["keyword"]
        city = sub.get("city", "")
        tracking = store.tracking_state_get(sub["id"], user_id)
        seen: set[str] = {str(s) for s in (tracking.get("seenIds") or [])}

        # ① 只读检索：节流 + 分层采样（复用 /api/jobs/search 同一口径）；
        #    结果写入检索缓存，通知里的岗位即可经既有「加入看板」链路回填岗位卡与匹配分
        boss_cli.limiter.wait()
        raw = await asyncio.to_thread(TOOLS.call, "search", keyword, city)
        normalized = [j for j in (jobs_router._normalize_job(r, keyword) for r in raw) if j]
        sampled = jobs_router._stratified_sample(normalized)
        store.jobs_cache_put(keyword, city, sampled)

        # ② diff 新岗位（对基线取差集；基线随每次运行滚动更新）
        new_jobs = [j for j in sampled if j["securityId"] not in seen]
        high_pushed = low_pushed = 0
        if new_jobs:
            scored = await _score_new_jobs(user_id, keyword, new_jobs)
            threshold = int(sub.get("minScore") or DEFAULT_MIN_SCORE)
            high = [(j, s, r) for j, s, r in scored if s >= threshold]
            low = [(j, s, r) for j, s, r in scored if s < threshold]
            # 推送口径（V2.12 确认）：高分逐条推「高分匹配」，低分汇总一条「低匹配新岗位」
            for job, score, reason in high[:HIGH_PUSH_LIMIT]:
                store.add_notification(user_id, "job_match", {
                    "keyword": keyword,
                    "city": city,
                    "securityId": job["securityId"],
                    "jobName": job["jobName"],
                    "brand": job["brand"],
                    "salary": job["salary"],
                    "experience": job.get("experience", ""),
                    "score": int(score),
                    "reason": reason,
                })
                high_pushed += 1
            if low:
                store.add_notification(user_id, "job_low_match", {
                    "keyword": keyword,
                    "city": city,
                    "count": len(low),
                    "samples": [j["jobName"] for j, _, _ in low[:3]],
                })
                low_pushed = 1

        # ③ 基线更新：本轮采样全部记为已见（未推送的溢出岗位同样入基线，保证幂等不重推）
        seen.update(j["securityId"] for j in sampled)
        tracking.update({
            "seenIds": sorted(seen),
            "lastRunAt": store.now_str(),
            "lastResult": {
                "sampled": len(sampled),
                "new": len(new_jobs),
                "highPushed": high_pushed,
                "lowPushed": low_pushed,
            },
        })
        store.tracking_state_put(tracking)
        return f"采样 {len(sampled)}，新增 {len(new_jobs)}，高分推 {high_pushed}，低分汇总 {low_pushed}"
    except _ABORT_ERRORS as exc:
        run.abort(f"环境不可用：{exc.detail}")
        return "整轮静默中止"
    # BossTimeoutError / BossUpstreamError 继续外抛：astep 记为该订阅失败步骤，继续其余订阅


def _probe_offline(run: AgentRun, user_id: str) -> str:
    """看板 zhipin 卡下架探测：按 last_probe_at 升序轮转取批（最久未探测优先）。

    判定口径（文档 6.2 测试重点「detail 失败 ≠ 必然下架」）：
    - 详情返回空/结构异常（BossUpstreamError）→ miss +1，跨日连续 OFFLINE_MISS_LIMIT 次才判定下架并推送提醒；
    - 超时（BossTimeoutError）→ 网络抖动，本轮不计数、不中止；
    - 环境类错误（未登录/风控/浏览器未就绪）→ 与订阅检索同口径，整轮静默中止。
    """
    try:
        cards = store.pipeline_cards(user_id)
        zhipin = [c for c in cards if (c.get("platform") or "zhipin") == "zhipin" and c.get("securityId")]
        rows = store.offline_watch_sync(user_id, zhipin)
        pending = sorted(
            (r for r in rows if not r["delisted"]),
            key=lambda r: r.get("lastProbeAt") or "",
        )
        batch = pending[: max(1, settings.tracking_probe_batch)]
        delisted_now = 0
        for row in batch:
            try:
                boss_cli.limiter.wait()
                detail = TOOLS.call("detail", row["securityId"])
                store.job_detail_put(row["securityId"], "", detail)  # 探测成功回写详情缓存（复用缓存机制）
                row["missCount"] = 0
            except boss_cli.BossUpstreamError:
                row["missCount"] = int(row.get("missCount") or 0) + 1
                if row["missCount"] >= OFFLINE_MISS_LIMIT:
                    row["delisted"] = True
                    row["notifiedAt"] = store.now_str()
                    store.add_notification(user_id, "job_delisted", {
                        "jobName": row.get("jobName", ""),
                        "brand": row.get("brand", ""),
                        "securityId": row["securityId"],
                        "cardId": row["cardId"],
                    })
                    delisted_now += 1
            row["lastProbeAt"] = store.now_str()
            store.offline_watch_put(row)
        return f"看板 zhipin 卡 {len(zhipin)}，本轮探测 {len(batch)}，下架 {delisted_now}"
    except _ABORT_ERRORS as exc:
        run.abort(f"环境不可用：{exc.detail}")
        return "整轮静默中止"


async def run_user_tracking(user_id: str, trigger: str = "manual") -> dict[str, Any]:
    """单用户一轮追踪（订阅推送 + 下架探测）；返回 AgentRun 汇总（含完整轨迹）。

    - trigger：manual（API 手动触发）/ beat（Celery 每日定时），仅用于轨迹标注；
    - 幂等：seenIds 基线 + delisted 标记，重复运行不重复推送。
    """
    run = AgentRun(f"job-tracking:{user_id}", ctx={"userId": user_id, "trigger": trigger})
    await run.astep("preflight", _preflight)
    subs = [s for s in store.subscriptions_of(user_id) if s.get("enabled")]
    run.ctx["subscriptions"] = len(subs)
    for sub in subs:
        if run.aborted:
            break
        await run.astep(
            f"sub:{sub['keyword']}|{sub.get('city', '')}",
            lambda r, s=sub: _track_subscription(r, user_id, s),
        )
    await run.astep("delist-probe", lambda r: _probe_offline(r, user_id))
    return run.summary()
