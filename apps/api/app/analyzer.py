"""引擎 A 输入侧：简历结构化 + 能力维度评估 + 题量预估（真实 LLM，主模型分层）。

由路由 `POST /api/resumes`（multipart 上传）调用；解析出的 `summary` 会作为
后续出题提示词的画像上下文（存 store.state.resume_summary，不对外回显）。

批 3 起：结构化输出一次多路消费——同一次解析同时产出简历体检报告
`checkup`（体检报告页 P12 与 AI 一键优化共用）；旧记录缺 checkup 时由
体检接口惰性补算（run_checkup）。
"""

import json
from typing import Any

from app import llm

# 题量三档（文档 6.x）：按简历信息量与岗位匹配度给出预估
COUNT_TIERS = (40, 80, 120)
DIMENSION_RANGE = (4, 6)

_SYSTEM = """你是资深面试官兼简历分析师，负责把简历转成「可出题的结构化画像」。
只输出一个 JSON 对象，不要任何解释性文字、不要 Markdown 围栏。

JSON 字段：
- targetRole: 字符串，求职意向岗位（简历未写明时依据经历推断）
- years: 数字，折算全职工作年限；在校/应届按实习时长折半计
- dimensions: 数组，4~6 项能力维度评估，每项 {"label": 维度名, "score": 0~100 整数}
  维度名从"专业技能、项目深度、工程实践、数据分析、沟通协作、行业认知、学习潜力"中按简历证据选取
- estimatedCount: 整数，建议出题量，只能取 40 / 80 / 120
  （信息量少或经历单薄取 40；内容充实、技术栈与项目丰富取 80；有深度项目细节与量化成果取 120）
- summary: 字符串，300 字以内的画像摘要，必须包含：技术栈清单、主要项目及职责、
  可量化的成果、明显短板或需要深挖的疑点。这段摘要将直接用于生成面试题，要具体、可引用。
- checkup: 对象，简历体检报告（一次解析同时产出，供体检报告页与 AI 优化使用）：
  {"totalScore": 0~100 整数整体评分, "comment": 一句话总评（60 字以内）,
   "highlights": 2~4 条亮点（每条一句话，必须基于简历真实内容）,
   "improvements": 2~4 条待改进项（具体到内容缺失、表述空泛、量化不足等）,
   "suggestions": 2~4 条可执行的优化建议}"""


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _fallback_count(dimensions: list[dict[str, Any]]) -> int:
    """模型未给出可用题量时的兜底：按维度均分推断档位。"""
    if not dimensions:
        return COUNT_TIERS[0]
    average = sum(d["score"] for d in dimensions) / len(dimensions)
    if average >= 80:
        return COUNT_TIERS[2]
    if average >= 60:
        return COUNT_TIERS[1]
    return COUNT_TIERS[0]


def _str_list(raw: Any) -> list[str]:
    """体检清单字段清洗：仅保留非空字符串，单条限长。"""
    if not isinstance(raw, list):
        return []
    return [str(x).strip()[:80] for x in raw if str(x or "").strip()]


def _rule_checkup(dimensions: list[dict[str, Any]]) -> dict[str, Any]:
    """LLM 未产出可用体检结论时的规则兜底：按维度均分给评分与模板文案。"""
    scores = [d["score"] for d in dimensions if isinstance(d, dict)]
    total = int(round(sum(scores) / len(scores))) if scores else 60
    total = max(0, min(100, total))
    weak = sorted(dimensions, key=lambda d: d["score"])[:2]
    weak_labels = "、".join(d["label"] for d in weak) if weak else "专业基础"
    return {
        "totalScore": total,
        "comment": (f"整体评分 {total} 分，简历具备基本竞争力，"
                    f"建议重点补强「{weak_labels}」相关内容。"),
        "highlights": ["已具备清晰的求职意向与基本经历结构。"],
        "improvements": [f"「{weak_labels}」维度得分偏低，缺少可量化成果支撑。"],
        "suggestions": ["为每段经历补充量化数据（规模、增长、效率提升）。",
                        "按目标岗位关键词调整技能与项目描述。"],
    }


def _normalize_checkup(raw: Any, dimensions: list[dict[str, Any]]) -> dict[str, Any]:
    """体检报告解析；字段残缺时回退规则版（不阻塞主链路）。"""
    if isinstance(raw, dict):
        highlights = _str_list(raw.get("highlights"))
        improvements = _str_list(raw.get("improvements"))
        suggestions = _str_list(raw.get("suggestions"))
        comment = str(raw.get("comment") or "").strip()
        if comment and highlights and improvements and suggestions:
            return {
                "totalScore": _clamp(raw.get("totalScore"), 0, 100, 60),
                "comment": comment[:120],
                "highlights": highlights[:4],
                "improvements": improvements[:4],
                "suggestions": suggestions[:4],
            }
    return _rule_checkup(dimensions)


def _normalize(payload: Any, file_name: str) -> tuple[dict[str, Any], str]:
    if not isinstance(payload, dict):
        raise llm.LlmError("简历结构化输出不是 JSON 对象")

    raw_dimensions = payload.get("dimensions")
    dimensions: list[dict[str, Any]] = []
    if isinstance(raw_dimensions, list):
        for item in raw_dimensions:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            if not label:
                continue
            dimensions.append({"label": label[:12], "score": _clamp(item.get("score"), 0, 100, 60)})
    dimensions = dimensions[: DIMENSION_RANGE[1]]
    if len(dimensions) < DIMENSION_RANGE[0]:
        raise llm.LlmError("能力维度评估不足 4 项，模型输出不可用")

    count = _clamp(payload.get("estimatedCount"), COUNT_TIERS[0], COUNT_TIERS[-1], 0)
    if count not in COUNT_TIERS:
        count = _fallback_count(dimensions)

    target_role = str(payload.get("targetRole") or "").strip()[:30] or "目标岗位"
    summary = str(payload.get("summary") or "").strip()
    analysis = {
        "fileName": file_name,
        # schema 固定为 int：折算年限四舍五入（应届+实习可能落到 0，前端按「应届」呈现）
        "years": _clamp(payload.get("years"), 0, 50, 0),
        "targetRole": target_role,
        "estimatedCount": count,
        "dimensions": dimensions,
        "checkup": _normalize_checkup(payload.get("checkup"), dimensions),
    }
    return analysis, summary or f"目标岗位：{target_role}"


async def analyze_resume(text: str, file_name: str) -> tuple[dict[str, Any], str]:
    """返回 (ResumeAnalysis 契约字段, 用于出题的画像摘要)。"""
    payload = await llm.chat_json(
        "primary",
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"以下是简历原文（已抽取纯文本）：\n\n{text[:12000]}"},
        ],
        temperature=0.2,
        # 推理型模型（如 deepseek-flash）会把思考 token 计入输出，预算给小了会 finish_reason=length 截断
        max_tokens=8192,
    )
    return _normalize(payload, file_name)


_CHECKUP_SYSTEM = """你是资深面试官，负责给一份简历做「体检评分」。
只输出一个 JSON 对象，不要任何解释性文字、不要 Markdown 围栏。

JSON 字段：
- totalScore: 0~100 整数，简历整体竞争力评分
- comment: 一句话总评（60 字以内）
- highlights: 2~4 条亮点，每条一句话，必须基于简历真实内容
- improvements: 2~4 条待改进项，具体到内容缺失、表述空泛、量化不足等
- suggestions: 2~4 条可执行的优化建议"""


async def run_checkup(text: str, dimensions: list[dict[str, Any]]) -> dict[str, Any]:
    """体检评分：旧简历记录缺 checkup 时由体检接口惰性补算调用。

    LLM 失败时回退规则兜底，不阻塞体检接口主链路。
    """
    try:
        payload = await llm.chat_json(
            "primary",
            [
                {"role": "system", "content": _CHECKUP_SYSTEM},
                {"role": "user", "content": f"以下是简历原文（已抽取纯文本）：\n\n{text[:12000]}"},
            ],
            temperature=0.2,
            max_tokens=4096,
        )
    except llm.LlmError:
        return _rule_checkup(dimensions)
    return _normalize_checkup(payload, dimensions)


_OPTIMIZE_SYSTEM = """你是资深简历顾问，负责按体检结论重写优化一份简历。
只输出一个 JSON 对象，不要任何解释性文字、不要 Markdown 围栏。

要求：
- resume: 优化后的完整简历纯文本。保留原有真实经历、不得虚构；逐条落实体检
  建议（补量化、去空泛、对齐目标岗位关键词）；用简洁段落与「·」列表组织，
  不要使用 Markdown 标记
- checkup: 优化后简历的体检报告 {"totalScore": 0~100 整数, "comment": 一句话总评,
  "highlights": 2~4 条, "improvements": 0~2 条剩余待改进,
  "suggestions": 0~2 条下一步建议}"""


async def optimize_resume(text: str, summary: str,
                          checkup: dict[str, Any]) -> dict[str, Any]:
    """AI 一键优化：以体检结论 + 画像摘要为输入主模型重写。

    返回 {"text": 优化后简历全文, "checkup": 优化后体检结论}；
    LLM 失败抛 LlmError（由路由层转 502，不静默降级——优化必须真实重写）。
    """
    payload = await llm.chat_json(
        "primary",
        [
            {"role": "system", "content": _OPTIMIZE_SYSTEM},
            {"role": "user", "content": (
                f"画像摘要：{summary or '（无）'}\n\n"
                f"体检结论：{json.dumps(checkup, ensure_ascii=False)}\n\n"
                f"以下是简历原文：\n\n{text[:12000]}"
            )},
        ],
        temperature=0.4,
        max_tokens=8192,
    )
    if not isinstance(payload, dict):
        raise llm.LlmError("优化输出不是 JSON 对象")
    optimized = str(payload.get("resume") or "").strip()
    if len(optimized) < 50:
        raise llm.LlmError("优化输出缺少可用简历文本")
    return {"text": optimized, "checkup": _normalize_checkup(payload.get("checkup"), [])}
