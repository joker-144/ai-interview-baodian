"""引擎 C：JD 精准定制 —— 三通道解析（六期，文档 3.6 / 原型 P5）。

三输入通道在 ``POST /api/jd/parse`` 统一入口分派，产出同一「结构化 JD」：
- 粘贴链接：解析 BOSS 岗位 URL -> security_id -> job_detail（7 天缓存复用）；
- 上传截图：vision 分层多模态提取岗位要素 -> ``boss search "<岗位名> <公司名>"``
  反查锁定目标岗位 -> detail；反查失败/未命中降级用视觉提取结果，不阻断；
- 粘贴文本：LLM 结构化，不触达平台（链接失效 / 截图识别失败的终极兜底，
  覆盖非 BOSS 渠道岗位与看板手动卡）。

出题复用 ``source=jd_target`` 骨架（settings.inputChannel 分派 + 内联 JD，
见 question_sets / generation），匹配度报告见 ``POST /api/jd/match-report``。

合规口径（文档第五章第 7 条）：仅受控只读检索，不做批量采集；
截图通道仅在反查命中时触达平台，否则完全离线（视觉提取）。
"""

import asyncio
import base64
import binascii
import re
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import boss_cli, llm, store
from app.auth import get_current_user
from app.generation import _jd_digest

router = APIRouter(prefix="/api/jd", tags=["jd"])

# 通道常量（settings.inputChannel 同口径）
CHANNEL_LINK = "link"
CHANNEL_SCREENSHOT = "screenshot"
CHANNEL_TEXT = "text"

# 截图 data URI 上限（base64 字符数；≈ 9MB 原图，兼顾 vision 接口限制）
_MAX_IMAGE_CHARS = 13_000_000
_IMAGE_PREFIX_RE = re.compile(r"^data:image/(png|jpe?g|webp);base64,", re.IGNORECASE)
# 粘贴文本通道的最短有效 JD（太短无法结构化出题）
_MIN_TEXT_CHARS = 50
# 截图识别结果的最低有效门槛（岗位名非空且描述够长才视为识别成功）
_MIN_DESC_CHARS = 40
_VISION_TIMEOUT_SEC = 120


class JdParseRequest(BaseModel):
    """三通道统一解析请求（按 channel 取对应字段，其余忽略）。"""

    channel: Literal["link", "screenshot", "text"]
    url: str = Field(default="", max_length=2048)  # link 通道：BOSS 岗位页 URL
    imageBase64: str = Field(default="", max_length=13_500_000)  # screenshot 通道：data URI
    text: str = Field(default="", max_length=20000)  # text 通道：JD 原文


# ---------------- 通用工具 ----------------


def _pick(raw: dict[str, Any], *keys: str) -> str:
    """多候选字段名容错取值（CLI 版本间字段可能变化）。"""
    for key in keys:
        value = raw.get(key)
        if value:
            return str(value).strip()
    return ""


def _jd_from_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """boss detail 全量 JD -> 统一结构化 JD（与 generation 出题上下文同构）。"""
    jd = {
        **_jd_digest(detail),
        "brand": _pick(detail, "brandName", "brand", "companyName", "company"),
        "city": _pick(detail, "cityName", "city", "areaDistrict"),
    }
    return jd


def _empty_to_clean_jd(raw: Any, channel: str) -> dict[str, Any] | None:
    """LLM 结构化输出 -> 统一 JD；岗位名与描述不达门槛返回 None（视为识别失败）。"""
    if not isinstance(raw, dict):
        return None
    job_name = str(raw.get("jobName") or "").strip()
    desc = str(raw.get("desc") or "").strip()
    if not job_name or len(desc) < _MIN_DESC_CHARS:
        return None
    skills = raw.get("skills")
    skill_list = (
        [str(s).strip() for s in skills if str(s or "").strip()][:8]
        if isinstance(skills, list)
        else []
    )
    return {
        "jobName": job_name[:64],
        "brand": str(raw.get("brand") or "").strip()[:64],
        "city": str(raw.get("city") or "").strip()[:32],
        "salary": str(raw.get("salary") or "").strip()[:32],
        "experience": str(raw.get("experience") or "").strip()[:32],
        "skills": skill_list,
        # 文本/截图通道保留更长描述（出题唯一素材，截 2000 字控制提示词体量）
        "desc": desc[:2000],
        "channel": channel,
    }


# ---------------- 通道 1：粘贴链接 ----------------


def _security_id_from_url(url: str) -> str:
    """BOSS 岗位 URL -> security_id：query 优先，回退 job_detail 路径段。"""
    url = url.strip()
    if not url:
        raise HTTPException(status_code=422, detail="请粘贴 Boss 直聘岗位页链接")
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="链接格式无法解析，请复制岗位页完整网址") from exc
    host = (parsed.hostname or "").lower()
    if not host.endswith("zhipin.com"):
        raise HTTPException(status_code=422, detail="仅支持 Boss 直聘（zhipin.com）岗位链接，其他平台请用「粘贴文本」通道")

    security_id = (parse_qs(parsed.query).get("securityId") or [""])[0].strip()
    if security_id:
        return security_id
    match = re.search(r"/job_detail/([A-Za-z0-9%_+.-]+)", parsed.path)
    if match:
        return match.group(1)
    raise HTTPException(
        status_code=422,
        detail="未从链接中识别到岗位 ID：请在岗位详情页地址栏复制完整链接（含 securityId 参数），或改用「粘贴文本」通道",
    )


async def _parse_link(url: str) -> dict[str, Any]:
    """链接通道：security_id -> job_detail（7 天缓存）-> 统一 JD。"""
    security_id = _security_id_from_url(url)
    detail = store.job_detail_get(security_id)
    if detail is None:
        try:
            boss_cli.limiter.wait()
            detail = await asyncio.to_thread(boss_cli.job_detail, security_id)
            store.job_detail_put(security_id, "", detail)
        except boss_cli.BossError as exc:
            detail_text = exc.detail if not exc.recovery else f"{exc.detail}（恢复指引：{exc.recovery}）"
            raise HTTPException(status_code=exc.status_code, detail=detail_text) from exc
    if not isinstance(detail, dict):
        raise HTTPException(status_code=502, detail="岗位详情解析失败，请稍后重试或改用「粘贴文本」通道")
    jd = _jd_from_detail(detail)
    jd["channel"] = CHANNEL_LINK
    jd["securityId"] = security_id
    return {"jd": jd, "retrievalMatched": True}


# ---------------- 通道 2：上传截图 ----------------

_SCREENSHOT_PROMPT = """以下是一张招聘平台岗位详情页的截图。请从中提取结构化信息，只输出一个 JSON 对象，不要解释性文字与 Markdown 围栏：
{
  "jobName": "岗位名称",
  "brand": "公司名称",
  "city": "城市",
  "salary": "薪资范围，截图没有则为空串",
  "experience": "经验要求，没有则为空串",
  "skills": ["技能或业务标签，最多 8 个，没有则为空数组"],
  "desc": "岗位职责与任职要求的完整文字，按原顺序整理转写，不要总结缩写"
}
硬性要求：只依据截图可见内容提取，不得编造截图上没有的信息；desc 必须完整覆盖截图中的 JD 正文。"""


async def _extract_screenshot(data_uri: str) -> dict[str, Any]:
    """vision 分层多模态提取截图要素 -> 统一 JD；不可读时抛 LlmError / 422。"""
    match = _IMAGE_PREFIX_RE.match(data_uri.strip())
    if not match:
        raise HTTPException(status_code=422, detail="截图格式不支持：请上传 PNG / JPG / WebP 图片")
    if len(data_uri) > _MAX_IMAGE_CHARS:
        raise HTTPException(status_code=422, detail="截图过大（约超过 9MB），请裁剪或压缩后重试")
    try:
        image_bytes = base64.b64decode(data_uri.strip()[match.end():], validate=False)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="截图数据损坏，请重新上传") from exc
    if not image_bytes:
        raise HTTPException(status_code=422, detail="截图数据为空，请重新上传")

    mime = match.group(1).lower()
    mime = "jpeg" if mime == "jpg" else mime
    encoded = base64.b64encode(image_bytes).decode("ascii")
    content: list[dict[str, Any]] = [
        {"type": "text", "text": _SCREENSHOT_PROMPT},
        {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{encoded}"}},
    ]
    try:
        raw = await llm.chat_json(
            "vision",
            [{"role": "user", "content": content}],
            temperature=0.1,
            max_tokens=8192,
            timeout=_VISION_TIMEOUT_SEC,
            thinking=False,
        )
    except llm.LlmError as exc:
        raise HTTPException(status_code=502, detail=f"截图识别服务异常：{exc}") from exc
    jd = _empty_to_clean_jd(raw, CHANNEL_SCREENSHOT)
    if jd is None:
        raise HTTPException(
            status_code=422,
            detail="未能从截图中识别出有效 JD（岗位名或正文缺失）：请换更清晰的截图，或改用「粘贴文本」通道直接粘贴 JD 原文",
        )
    return jd


def _search_hit(jobs: list[dict[str, Any]], job_name: str, brand: str) -> str:
    """检索结果中锁定目标岗位：公司名+岗位名双命中优先，返回 security_id。"""
    job_key = job_name.replace(" ", "")
    brand_key = brand.replace(" ", "")
    fallback = ""
    for raw in jobs:
        security_id = _pick(raw, "securityId", "security_id", "encryptJobId", "jobId")
        name = _pick(raw, "jobName", "name", "positionName", "title")
        company = _pick(raw, "brandName", "brand", "companyName", "company_name")
        if not security_id or not name:
            continue
        name_key = name.replace(" ", "")
        company_key = company.replace(" ", "")
        if brand_key and company_key and brand_key in company_key and name_key in job_key:
            return security_id
        if name_key == job_key and not fallback:
            fallback = security_id
    return fallback


async def _parse_screenshot(data_uri: str) -> dict[str, Any]:
    """截图通道：视觉提取 -> boss search 反查锁定（失败降级视觉结果）。"""
    jd = await _extract_screenshot(data_uri)

    # 反查锁定：公司名+岗位名检索，命中则用全量 detail 覆盖（素材更全更准）
    matched_id = ""
    if jd["brand"]:
        try:
            boss_cli.limiter.wait()
            keyword = f"{jd['jobName']} {jd['brand']}".strip()
            raw_jobs = await asyncio.to_thread(boss_cli.search_jobs, keyword, jd["city"])
            matched_id = _search_hit(raw_jobs, jd["jobName"], jd["brand"])
        except boss_cli.BossError:
            matched_id = ""  # 反查失败不阻断：降级用视觉提取结果
    if not matched_id:
        return {"jd": jd, "retrievalMatched": False}

    try:
        detail = store.job_detail_get(matched_id)
        if detail is None:
            boss_cli.limiter.wait()
            detail = await asyncio.to_thread(boss_cli.job_detail, matched_id)
            store.job_detail_put(matched_id, "", detail)
    except boss_cli.BossError:
        return {"jd": jd, "retrievalMatched": False}
    if isinstance(detail, dict):
        full = _jd_from_detail(detail)
        full["channel"] = CHANNEL_SCREENSHOT
        full["securityId"] = matched_id
        return {"jd": full, "retrievalMatched": True}
    return {"jd": jd, "retrievalMatched": False}


# ---------------- 通道 3：粘贴文本 ----------------

_TEXT_PROMPT = """你是招聘信息解析助手。用户将粘贴一段岗位 JD 原文（可能来自任意招聘平台）。请提取结构化信息，只输出一个 JSON 对象，不要解释性文字与 Markdown 围栏：
{
  "jobName": "岗位名称",
  "brand": "公司名称，原文没有则为空串",
  "city": "城市，没有则为空串",
  "salary": "薪资范围，没有则为空串",
  "experience": "经验要求，没有则为空串",
  "skills": ["技能或业务标签，最多 8 个，没有则为空数组"],
  "desc": "岗位职责与任职要求整理稿：可清理格式噪声与无关文字，但保留全部实质内容，不要缩写删减"
}
硬性要求：只依据原文提取，不得编造原文没有的信息。"""


async def _parse_text(text: str) -> dict[str, Any]:
    """文本通道：LLM 结构化，不触达平台（终极兜底）。"""
    body = (text or "").strip()
    if len(body) < _MIN_TEXT_CHARS:
        raise HTTPException(status_code=422, detail=f"JD 原文太短（至少 {_MIN_TEXT_CHARS} 字）：请粘贴完整岗位职责与要求")
    try:
        raw = await llm.chat_json(
            "primary",
            [
                {"role": "system", "content": _TEXT_PROMPT},
                {"role": "user", "content": body[:12000]},
            ],
            temperature=0.2,
            max_tokens=6144,
            thinking=False,
        )
    except llm.LlmError as exc:
        raise HTTPException(status_code=502, detail=f"JD 结构化解析失败：{exc}") from exc
    jd = _empty_to_clean_jd(raw, CHANNEL_TEXT)
    if jd is None:
        raise HTTPException(status_code=422, detail="未能从粘贴文本中解析出有效岗位信息：请确认粘贴的是完整 JD 原文")
    return {"jd": jd, "retrievalMatched": False}


# ---------------- 统一入口 ----------------


@router.post("/parse")
async def parse_jd(body: JdParseRequest, user_id: str = Depends(get_current_user)) -> dict[str, Any]:
    """三通道统一解析：产出同一结构化 JD（供解析面板展示与 source=jd_target 出题）。"""
    if body.channel == CHANNEL_LINK:
        return await _parse_link(body.url)
    if body.channel == CHANNEL_SCREENSHOT:
        return await _parse_screenshot(body.imageBase64)
    return await _parse_text(body.text)


# ---------------- 岗位匹配度报告（文档 3.6 关键设计） ----------------


class JdMatchRequest(BaseModel):
    """即时匹配度评估：传入 /api/jd/parse 产出的结构化 JD。"""

    jd: dict = Field(default_factory=dict)


def _validate_jd(jd: dict) -> dict[str, Any]:
    """匹配度评估入参校验：jobName 与 desc 必填（其余字段可选）。"""
    job_name = str(jd.get("jobName") or "").strip()
    desc = str(jd.get("desc") or "").strip()
    if not job_name or len(desc) < 20:
        raise HTTPException(status_code=422, detail="JD 数据不完整：请先通过「解析」获得岗位信息再评估匹配度")
    return jd


@router.post("/match-report")
async def match_report(body: JdMatchRequest, user_id: str = Depends(get_current_user)) -> dict[str, Any]:
    """即时岗位匹配度评估（解析面板徽章 + 简版三分类预览，不落库）。

    完整报告随出题任务产出并挂题集（GET ?setId=）；本接口需要已上传简历。
    """
    from app import generation

    _validate_jd(body.jd)
    saved = store.user_resume(user_id) or {}
    has_resume = bool(
        str(saved.get("text") or "").strip() or str(saved.get("summary") or "").strip()
    )
    if not has_resume:
        raise HTTPException(status_code=422, detail="尚未上传简历：请先在「简历」页完成解析，再评估岗位匹配度")
    try:
        report = await generation.generate_match_report(body.jd, user_id)
    except llm.LlmError as exc:
        raise HTTPException(status_code=502, detail=f"匹配度评估失败：{exc}") from exc
    return {"report": report, "persisted": False}


@router.get("/match-report")
def match_report_by_set(setId: str, user_id: str = Depends(get_current_user)) -> dict[str, Any]:
    """题集关联的岗位匹配度报告（出题时随「结合我的简历生成」产出，落库可回看）。"""
    set_id = (setId or "").strip()
    if not set_id:
        raise HTTPException(status_code=422, detail="需要提供 setId（目标题集）")
    rec = store.jd_report_by_set(user_id, set_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="该题集暂无匹配度报告：开启「结合我的简历生成」重新出题即可生成")
    return {
        "reportId": rec["reportId"],
        "setId": rec.get("setId") or "",
        "jobName": rec.get("jobName", ""),
        "brand": rec.get("brand", ""),
        "matchScore": rec.get("matchScore", 0),
        "report": rec.get("report") or {},
        "createdAt": rec.get("createdAt", ""),
        "persisted": True,
    }
