"""引擎 A 输入侧：简历结构化 + 能力维度评估 + 题量预估（真实 LLM，主模型分层）。

由路由 `POST /api/resumes`（multipart 上传）调用；解析出的 `summary` 会作为
后续出题提示词的画像上下文（存 store.state.resume_summary，不对外回显）。
"""

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
  可量化的成果、明显短板或需要深挖的疑点。这段摘要将直接用于生成面试题，要具体、可引用。"""


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
