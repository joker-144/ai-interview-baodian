"""岗位检索（引擎 B 接入层，三期，文档 3.5 / 原型 P4）。

链路：登录态探测 -> 关键词检索（boss-agent-cli 子进程，JSON 信封）-> 分层采样
（按薪资带分组轮取 + 单公司去重，避免单一公司偏差）-> 岗位卡缓存（TTL 7 天）。

- 我侧仅只读检索（status / search / detail），不做批量采集等动作；
- 未安装 CLI / 未登录 / 风控命中时抛结构化 HTTP 错误，前端展示引导与恢复指令；
- 出题链路（job_search 生成任务 / 考点地图）在批 2 落地，本文件只做检索与缓存。
"""

import asyncio
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import boss_browser, boss_cli, store
from app.auth import get_current_user

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# 页面固定展示的数据来源声明（原型 P4 口径；TTL 与 store.JOBS_CACHE_TTL_DAYS 保持一致）
DATA_SOURCE = "数据来自 Boss 直聘 · 实时检索 · 缓存 1 天"
# 分层采样目标条数（文档 3.5：默认 15~20 个岗位）
SAMPLE_TARGET = 18
# 同一公司最多进入采样结果的岗位数（避免单一公司霸榜）
_PER_COMPANY_CAP = 2


class JobSearchRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=64)
    city: str = Field(default="", max_length=32)


def _pick(raw: dict[str, Any], *keys: str) -> str:
    """多候选字段名容错取值（CLI 版本间字段可能变化）。"""
    for key in keys:
        value = raw.get(key)
        if value:
            return str(value).strip()
    return ""


def _parse_salary_low(salary: str) -> float | None:
    """薪资下限（K）：'25-50K·16薪' -> 25.0；解析失败返回 None（归入中档）。"""
    match = re.search(r"(\d+(?:\.\d+)?)\s*[-~到]", salary or "")
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    return value  # 元/天等口径数值远小于 K，分档时自然落入低档，无需换算


def _normalize_job(raw: dict[str, Any], keyword: str) -> dict[str, Any] | None:
    """原始岗位 -> 前端岗位卡；缺 security_id 或岗位名的条目丢弃。"""
    security_id = _pick(raw, "securityId", "security_id", "encryptJobId", "jobId")
    job_name = _pick(raw, "jobName", "name", "positionName", "title")
    if not security_id or not job_name:
        return None
    return {
        "securityId": security_id,
        "jobName": job_name,
        "salary": _pick(raw, "salaryDesc", "salary", "salaryString", "salary_desc") or "面议",
        "brand": _pick(raw, "brandName", "brand", "companyName", "company_name", "company"),
        "city": _pick(raw, "cityName", "city", "areaDistrict"),
        "experience": _pick(raw, "jobExperience", "experience", "expName"),
        "education": _pick(raw, "jobDegree", "degree", "educationName", "education"),
        "skills": _skills_of(raw),
        "industry": _pick(raw, "brandIndustry", "industry", "industryName"),
        # 岗位要求/职责描述片段（检索列表未必携带，空串容忍；全量 JD 走 /{security_id}/detail）
        "description": _pick(raw, "jobDesc", "description", "jobDescription", "content", "desc"),
        "keyword": keyword,
    }


def _skills_of(raw: dict[str, Any]) -> list[str]:
    """业务/技能标签（B端产品、增长策略…），最多 6 个。"""
    for key in ("skills", "skillTags", "labels", "jobLabels", "job_labels", "tags"):
        value = raw.get(key)
        if isinstance(value, list) and value:
            return [str(v).strip() for v in value if str(v or "").strip()][:6]
    return []


def _stratified_sample(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """分层采样：按薪资下限三分位分低/中/高三档，档内轮取 + 单公司限额。

    无薪资数据（解析失败）的岗位并入中档兜底，保证总量充足。
    """
    for job in jobs:
        job["_band"] = _parse_salary_low(job["salary"])
    with_salary = sorted((j for j in jobs if j["_band"] is not None), key=lambda j: j["_band"])
    without = [j for j in jobs if j["_band"] is None]

    thirds = max(len(with_salary) // 3, 1)
    bands = [
        with_salary[:thirds],
        with_salary[thirds : thirds * 2] + without,
        with_salary[thirds * 2 :],
    ]

    picked: list[dict[str, Any]] = []
    company_count: dict[str, int] = {}
    seen_ids: set[str] = set()

    def take(pool: list[dict[str, Any]]) -> None:
        for job in pool:
            if len(picked) >= SAMPLE_TARGET:
                return
            if job["securityId"] in seen_ids:
                continue
            brand = job["brand"] or "_"
            if company_count.get(brand, 0) >= _PER_COMPANY_CAP:
                continue
            seen_ids.add(job["securityId"])
            company_count[brand] = company_count.get(brand, 0) + 1
            picked.append(job)

    # 低/中/高三档轮转，薪资带覆盖均匀
    for round_index in range(SAMPLE_TARGET):
        for band in bands:
            if round_index < len(band):
                take([band[round_index]])
            if len(picked) >= SAMPLE_TARGET:
                break
        if len(picked) >= SAMPLE_TARGET:
            break

    for job in picked:
        job.pop("_band", None)
    return picked


@router.get("/status")
def job_status(user_id: str = Depends(get_current_user)) -> dict[str, Any]:
    """CLI 安装状态 + Boss 登录态 + 专用浏览器连接态（前端引导卡数据源）。"""
    browser = boss_browser.browser_status()
    return {
        **boss_cli.boss_status(),
        "browserConnected": browser["connected"],
        "browserHint": browser["hint"],
        "browserStartCommand": browser["startCommand"],
    }


@router.post("/browser/start")
def start_boss_browser(user_id: str = Depends(get_current_user)) -> dict[str, Any]:
    """按需拉起专用 Chrome（持久化 profile + CDP 9222 + 打开 BOSS 直聘）。

    供前端「启动浏览器」按钮调用；仅在用户显式触发时 spawn，后端不会自动拉起。
    登录态保存在专用 profile，登录一次即可长期复用（借鉴 BossHunter 的一键启动器）。
    """
    result = boss_browser.launch_dedicated_chrome()
    if not result.get("reachable") and not result.get("launched"):
        raise HTTPException(status_code=503, detail=result.get("detail") or "无法启动专用 Chrome")
    return result


@router.post("/search")
async def search_jobs(
    body: JobSearchRequest, user_id: str = Depends(get_current_user)
) -> dict[str, Any]:
    """关键词检索 -> 分层采样岗位卡（缓存 1 天，同关键词+城市当天直接命中）。"""
    keyword = body.keyword.strip()
    city = body.city.strip()
    cache_key = store.jobs_cache_key(keyword, city)

    hit = store.jobs_cache_get(cache_key)
    if hit:
        return {
            "keyword": hit["keyword"],
            "city": hit["city"],
            "jobs": hit["payload"],
            "dataSource": DATA_SOURCE,
            "cached": True,
        }

    try:
        boss_cli.limiter.wait()  # 我侧最小间隔（附加保险，主节流在 CLI 内置）
        raw_jobs = await asyncio.to_thread(boss_cli.search_jobs, keyword, city)
    except boss_cli.BossError as exc:
        detail = exc.detail if not exc.recovery else f"{exc.detail}（恢复指引：{exc.recovery}）"
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc

    normalized = [job for job in (_normalize_job(j, keyword) for j in raw_jobs) if job]
    if not normalized:
        raise HTTPException(status_code=502, detail="检索结果解析失败（可能接口漂移），请稍后重试")
    sampled = _stratified_sample(normalized)
    store.jobs_cache_put(keyword, city, sampled)
    return {"keyword": keyword, "city": city, "jobs": sampled, "dataSource": DATA_SOURCE, "cached": False}


@router.get("/map")
def job_map(
    keyword: str, city: str = "", user_id: str = Depends(get_current_user)
) -> dict[str, Any]:
    """岗位考点地图（TOP 技能榜 + 三档分布；来源聚合缓存，未生成过 404 引导先生成）。"""
    kw = keyword.strip()
    if not kw:
        raise HTTPException(status_code=422, detail="需要提供 keyword（目标岗位关键词）")
    agg = store.job_map_get(store.jobs_cache_key(kw, city.strip()))
    if agg is None:
        raise HTTPException(
            status_code=404,
            detail="该关键词尚未生成考点地图：请先检索岗位并触发生成题库，完成后即可查看",
        )
    return {**agg, "keyword": kw, "city": city.strip(), "dataSource": DATA_SOURCE}


@router.get("/{security_id}/detail")
async def get_job_detail(
    security_id: str, user_id: str = Depends(get_current_user)
) -> dict[str, Any]:
    """单岗位全量 JD（缓存 7 天；供前端详情与批 2 聚合分析复用）。"""
    cached = store.job_detail_get(security_id)
    if cached is not None:
        return {"detail": cached, "dataSource": DATA_SOURCE, "cached": True}
    try:
        boss_cli.limiter.wait()
        detail = await asyncio.to_thread(boss_cli.job_detail, security_id)
    except boss_cli.BossError as exc:
        detail_text = exc.detail if not exc.recovery else f"{exc.detail}（恢复指引：{exc.recovery}）"
        raise HTTPException(status_code=exc.status_code, detail=detail_text) from exc
    store.job_detail_put(security_id, "", detail)
    return {"detail": detail, "dataSource": DATA_SOURCE, "cached": False}
