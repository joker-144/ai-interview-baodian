"""引擎 A 出题链路（真实 LLM）。

流程：覆盖矩阵规划 → 分批并发调用主模型 → 结构校验/归一化 → 轻量模型答案二次校验
→ 与运行时题库去重（本地 Embedding 语义去重，失败降级为文本相似度）→ 落库。

一期口径：
- 只产出单选客观题（刷题页判分链路只支持单选；多选/简答随题型扩展一起做）；
- 生成题不伪造全站答对率（siteCorrectRate=None，走低样本保护不展示）；
- 生成结果写在内存题库（store.state.questions），重启回到种子态。
"""

import asyncio
import re
from difflib import SequenceMatcher
from typing import Any

from app import llm, local_models, store

# 单次调用的题量：深度思考模型输出较长（含 reasoning token），4 题约 2~3k tokens
BATCH_SIZE = 4
MAX_CONCURRENCY = 3
OPTION_COUNT = 4
# 语义去重阈值（向量已归一化，点积即余弦相似度）
DEDUP_SIMILARITY = 0.88
# 文本相似度兜底阈值（Embedding 不可用时）
DEDUP_TEXT_RATIO = 0.85

_LETTERS = "ABCD"

_GEN_SYSTEM = """你是资深技术面试官，负责依据候选人简历出「单选题」。
只输出 JSON 数组，不要解释性文字、不要 Markdown 围栏。

数组每个元素结构：
{
  "stem": "题干，一句到两句，明确问一个知识点或场景决策",
  "options": ["选项1", "选项2", "选项3", "选项4"],
  "answer": "A/B/C/D 中的一个字母，必须是唯一正确答案",
  "explanation": "解析，说明正确项为何对、干扰项错在哪，120 字以内",
  "referenceAnswer": "候选人被追问时的口头作答要点，150 字以内，分条陈述",
  "knowledgeTags": ["知识点标签，2~4 个"],
  "difficulty": 1/2/3（1=基础 2=进阶 3=挑战）
}

硬性要求：
- 选项必须恰好 4 个，且互不重叠、长度相近，干扰项要似是而非而非明显错误；
- 题干必须与候选人简历中的具体技术栈/项目结合，避免与「已有题目」重复或高度相似；
- 不要在选项文本里加 A./B. 前缀，也不要在题干里透露答案；
- 所有内容用中文（技术专有名词保留英文）。"""

_VERIFY_SYSTEM = """你是严谨的审题人。给定若干单选题，请独立判断每题的正确答案，
并检查题干是否存在歧义、选项是否有两个及以上都成立。
只输出 JSON 数组，不要解释性文字、不要 Markdown 围栏：
[{"index": 0, "answer": "独立判断的字母", "agree": true/false, "issue": "若有问题，一句话说明"}]
agree 仅当「你的答案与题目给定答案一致」且「题干选项无歧义」时为 true。"""


def _plan(count: int) -> list[tuple[str, int]]:
    """覆盖矩阵：把题量轮转分配到 6 个维度，保证每维都有覆盖。"""
    dimensions = store.GENERATE_DIMENSIONS
    plan: list[tuple[str, int]] = []
    remaining = count
    index = 0
    while remaining > 0:
        dimension = dimensions[index % len(dimensions)]
        take = min(BATCH_SIZE, remaining)
        plan.append((dimension, take))
        remaining -= take
        index += 1
    return plan


def _stem_key(text: str) -> str:
    """归一化题干：去空白与标点，用于精确去重。"""
    return re.sub(r"[\s\W_]+", "", text or "").lower()


def _opt_key(text: str) -> str:
    """去掉模型可能自带的 "A." / "A、" 前缀。"""
    return re.sub(r"^\s*[A-Da-d][.、．)）]\s*", "", str(text or "")).strip()


def _resume_context() -> str:
    analysis = store.state.resume_analysis or {}
    parts = []
    if analysis:
        parts.append(
            f"目标岗位：{analysis.get('targetRole', '未知')}；"
            f"折算年限：{analysis.get('years', 0)} 年；"
            f"能力维度：{('、'.join(f'{d['label']}{d['score']}' for d in analysis.get('dimensions', []))) or '未知'}"
        )
    if store.state.resume_summary:
        parts.append(f"画像摘要：{store.state.resume_summary}")
    if not parts:
        parts.append("暂无简历画像，按通用技术面试题生成。")
    return "\n".join(parts)


def _normalize(raw: Any, dimension: str) -> dict[str, Any] | None:
    """结构校验 + 归一化；不满足硬性要求的题目直接丢弃（返回 None）。"""
    if not isinstance(raw, dict):
        return None
    stem = str(raw.get("stem") or "").strip()
    raw_options = raw.get("options")
    if not stem or not isinstance(raw_options, list):
        return None
    options = [_opt_key(o) for o in raw_options if str(o or "").strip()]
    if len(options) != OPTION_COUNT:
        return None
    if len(set(options)) != OPTION_COUNT:  # 选项重复
        return None

    answer = str(raw.get("answer") or "").strip().upper()
    if answer and answer not in _LETTERS:
        # 模型返回选项全文时，回落到匹配选项文本
        matched = next((i for i, o in enumerate(options) if o == _opt_key(answer)), None)
        if matched is None:
            return None
        answer = _LETTERS[matched]
    if answer not in _LETTERS:
        return None

    tags = raw.get("knowledgeTags")
    knowledge_tags = [str(t).strip()[:20] for t in tags if str(t or "").strip()][:4] if isinstance(tags, list) else []
    explanation = str(raw.get("explanation") or "").strip()
    reference = str(raw.get("referenceAnswer") or "").strip() or explanation
    if not explanation:
        return None

    difficulty = raw.get("difficulty")
    try:
        difficulty = max(1, min(3, int(difficulty)))
    except (TypeError, ValueError):
        difficulty = 2

    return {
        "stem": stem,
        "options": options,
        "answer": answer,
        "explanation": explanation,
        "referenceAnswer": reference,
        "knowledgeTags": knowledge_tags,
        "difficulty": difficulty,
        "category": dimension,
    }


async def _generate_batch(dimension: str, count: int, avoid: list[str]) -> list[dict[str, Any]]:
    avoid_hint = ""
    if avoid:
        avoid_hint = "\n\n以下题干已存在，请勿重复或高度相似：\n" + "\n".join(f"- {s[:60]}" for s in avoid[-20:])

    user_prompt = (
        f"候选人画像：\n{_resume_context()}\n\n"
        f"本次请生成 {count} 道题，全部聚焦「{dimension}」这一维度。"
        f"难度分布覆盖 1~3 档，其中至少 1 道为 3 档。{avoid_hint}"
    )
    items = await llm.chat_json(
        "primary",
        [
            {"role": "system", "content": _GEN_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.6,
        # 4 题 + 解析 + 参考回答，且推理模型思考 token 计入输出，预算需留足
        max_tokens=12288,
    )
    if not isinstance(items, list):
        raise llm.LlmError("出题输出不是 JSON 数组")

    normalized = [q for q in (_normalize(item, dimension) for item in items) if q]
    if not normalized:
        raise llm.LlmError("本批题目未通过结构校验")
    return normalized


async def _verify(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """答案二次校验：独立复判，答案不一致或题干有歧义的题目丢弃。"""
    if not questions:
        return []
    payload = [
        {"index": i, "stem": q["stem"], "options": q["options"], "answer": q["answer"]}
        for i, q in enumerate(questions)
    ]
    verdicts = await llm.chat_json(
        "light",
        [
            {"role": "system", "content": _VERIFY_SYSTEM},
            {"role": "user", "content": f"题目如下：\n{payload}"},
        ],
        temperature=0.0,
        max_tokens=4096,
    )
    if not isinstance(verdicts, list):
        raise llm.LlmError("校验输出不是 JSON 数组")

    agreed: dict[int, bool] = {}
    for item in verdicts:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        answer = str(item.get("answer") or "").strip().upper()
        agreed[index] = bool(item.get("agree")) and answer == questions[index]["answer"]
    # 未被复判到的题目按通过处理（避免因输出缺项整批作废）
    return [q for i, q in enumerate(questions) if agreed.get(i, True)]


def _text_is_duplicate(stem: str, existing_keys: list[str]) -> bool:
    key = _stem_key(stem)
    if key in existing_keys:
        return True
    return any(SequenceMatcher(None, key, other).ratio() > DEDUP_TEXT_RATIO for other in existing_keys)


def _embedding_is_duplicate(stems: list[str], existing_stems: list[str]) -> set[int]:
    """本地 Embedding 语义去重（不可用时抛异常，由调用方降级）。"""
    cfg = store.state.llm_config.get("embedding") or {}
    if cfg.get("provider") != "local":
        raise RuntimeError("Embedding 分层未使用本地模型，跳过语义去重")

    vectors = local_models.embed(stems + existing_stems)["vectors"]
    new_vectors = vectors[: len(stems)]
    old_vectors = vectors[len(stems) :]
    dropped: set[int] = set()
    for i, vector in enumerate(new_vectors):
        for other in old_vectors:
            dot = sum(a * b for a, b in zip(vector, other))
            if dot >= DEDUP_SIMILARITY:
                dropped.add(i)
                break
    return dropped


async def _dedupe(questions: list[dict[str, Any]], existing_stems: list[str]) -> list[dict[str, Any]]:
    """先去内部重复，再与运行时题库比对：能跑 Embedding 就跑语义去重，否则文本相似度兜底。"""
    existing_keys = [_stem_key(s) for s in existing_stems]
    unique: list[dict[str, Any]] = []
    for question in questions:
        if _text_is_duplicate(question["stem"], existing_keys):
            continue
        existing_keys.append(_stem_key(question["stem"]))
        unique.append(question)

    if not unique:
        return []
    try:
        dropped = await asyncio.to_thread(
            _embedding_is_duplicate,
            [q["stem"] for q in unique],
            existing_stems,
        )
    except Exception:
        return unique  # 语义去重不可用时不阻断出题
    return [q for i, q in enumerate(unique) if i not in dropped]


def _to_question(item: dict[str, Any], set_id: str) -> dict[str, Any]:
    return {
        "id": store.new_id("q-gen"),
        "setId": set_id,
        "type": "single_choice",
        "category": item["category"],
        "stem": item["stem"],
        "options": item["options"],
        "answer": item["answer"],
        "explanation": item["explanation"],
        "referenceAnswer": item["referenceAnswer"],
        "knowledgeTags": item["knowledgeTags"],
        "difficulty": item["difficulty"],
        "siteCorrectRate": None,  # 生成题无作答样本，走低样本保护不展示全站答对率
    }


async def run_generation(task: store.GenerateTask, set_id: str) -> None:
    """后台出题任务：单批失败不丢弃已成功批次，全部结束后置 done。"""
    try:
        plan = _plan(task.total)
        primary_cfg = store.state.llm_config.get("primary") or {}
        concurrency = max(1, min(int((primary_cfg.get("params") or {}).get("concurrency", 3)), MAX_CONCURRENCY))
        semaphore = asyncio.Semaphore(concurrency)
        existing_stems = [q["stem"] for q in store.all_questions()]

        async def worker(dimension: str, count: int, dimension_index: int) -> None:
            async with semaphore:
                batch = await _generate_batch(dimension, count, existing_stems)
                verified = await _verify(batch)
                accepted = await _dedupe(verified, existing_stems)
                with task.lock:
                    if accepted:
                        store.add_questions([_to_question(item, set_id) for item in accepted], set_id)
                        existing_stems.extend(item["stem"] for item in accepted)
                    task.generated += len(accepted)
                    task.dropped += len(batch) - len(accepted)
                    task.dimension_index = dimension_index

        results = await asyncio.gather(
            *(worker(dimension, count, index) for index, (dimension, count) in enumerate(plan)),
            return_exceptions=True,
        )
        failures = [r for r in results if isinstance(r, BaseException)]
        if failures:
            task.error = f"{len(failures)}/{len(plan)} 批生成失败：{failures[0]}"
        if task.generated == 0 and not task.error:
            task.error = "本次未产出任何题目（全部被去重或校验淘汰），请重试"
    except Exception as exc:  # 兜底：避免任务永远停在未完成态
        task.error = f"出题任务异常：{exc}"
    finally:
        with task.lock:
            task.done = True


def start(task: store.GenerateTask, set_id: str) -> None:
    """在事件循环内启动后台协程（句柄挂在任务对象上防止被 GC）。"""
    task.set_id = set_id
    task.asyncio_handle = asyncio.create_task(run_generation(task, set_id))
