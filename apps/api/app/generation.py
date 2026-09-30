"""引擎 A 出题链路（真实 LLM）。

流程：覆盖矩阵规划 → 分批并发调用主模型 → 结构校验/归一化 → 轻量模型答案二次校验
→ 与运行时题库去重（本地 Embedding 语义去重，失败降级为文本相似度）→ 落库。

一期口径：
- 只产出单选客观题（刷题页判分链路只支持单选；多选/简答随题型扩展一起做）；
- 生成题不伪造全站答对率（siteCorrectRate=None，走低样本保护不展示）；
- 生成结果写在内存题库（store.state.questions），重启回到种子态。
"""

import asyncio
import json
import re
from difflib import SequenceMatcher
from typing import Any

from app import db, llm, local_models, store

# 单次调用的题量：4 题（含解析与参考回答）约 2~3k tokens 输出
BATCH_SIZE = 4
# 与 llm.json primary.params.concurrency 对齐（run_generation 取两者较小值）
MAX_CONCURRENCY = 4
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


def _resume_context(user_id: str) -> str:
    saved = store.user_resume(user_id) or {}
    analysis = saved.get("analysis") or {}
    parts = []
    if analysis:
        parts.append(
            f"目标岗位：{analysis.get('targetRole', '未知')}；"
            f"折算年限：{analysis.get('years', 0)} 年；"
            f"能力维度：{('、'.join(f'{d['label']}{d['score']}' for d in analysis.get('dimensions', []))) or '未知'}"
        )
    if saved.get("summary"):
        parts.append(f"画像摘要：{saved['summary']}")
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


async def _generate_batch(dimension: str, count: int, avoid: list[str], user_id: str) -> list[dict[str, Any]]:
    avoid_hint = ""
    if avoid:
        avoid_hint = "\n\n以下题干已存在，请勿重复或高度相似：\n" + "\n".join(f"- {s[:60]}" for s in avoid[-20:])

    user_prompt = (
        f"候选人画像：\n{_resume_context(user_id)}\n\n"
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
        # 4 题 + 解析 + 参考回答约 2~3k tokens，12288 留足余量
        max_tokens=12288,
        # 非思考模式：思维链 token 计入输出曾致批次截断，且思考延迟拖慢整体吞吐
        thinking=False,
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
        # 复判是轻量结构化任务，同样走非思考模式（提速 + 避免思维链挤占预算）
        thinking=False,
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
        # 预备批次：去重/答案复判会淘汰部分产出（实测约 5%~15%），按 25% 余量
        # 规划批次；满额后剩余批次直接跳过，保证交付量贴近 task.total
        margin = max(2, task.total // 4)
        plan = _plan(task.total + margin)
        primary_cfg = store.state.llm_config.get("primary") or {}
        concurrency = max(1, min(int((primary_cfg.get("params") or {}).get("concurrency", 3)), MAX_CONCURRENCY))
        semaphore = asyncio.Semaphore(concurrency)
        existing_stems = [q["stem"] for q in store.all_questions()]

        async def worker(dimension: str, count: int, dimension_index: int) -> None:
            async with semaphore:
                with task.lock:
                    if task.generated >= task.total:
                        return  # 已满额：预备批次直接跳过，省一次 LLM 调用
                # 结构化输出偶发不合规范（非数组 / 整批未过校验 / 截断）：
                # 同参数重试一次即可恢复绝大多数，仍失败才计为批次失败
                try:
                    batch = await _generate_batch(dimension, count, existing_stems, task.user_id)
                except llm.LlmError:
                    batch = await _generate_batch(dimension, count, existing_stems, task.user_id)
                try:
                    verified = await _verify(batch)
                except llm.LlmError:
                    verified = await _verify(batch)
                accepted = await _dedupe(verified, existing_stems)
                with task.lock:
                    # 满额裁剪：交付与进度口径始终不超过 task.total
                    accepted = accepted[: max(task.total - task.generated, 0)]
                    if accepted:
                        store.add_questions([_to_question(item, set_id) for item in accepted], set_id)
                        existing_stems.extend(item["stem"] for item in accepted)
                        task.generated += len(accepted)
                    task.dropped += len(batch) - len(accepted)
                    task.dimension_index = dimension_index
                    # 每批同步一次 DB 进度：进程被杀时 generated_cnt 停在最后一批（中断恢复口径）
                    db.update_generate_progress(task.task_id, task.generated, task.dropped)

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
        # 终态落库（done + error）；恢复任务不会进入本函数，无二次覆盖问题
        db.finish_generate_task(task.task_id, task.error)
        # 站内信：出题完成（批 4；失败不发，避免打扰）
        if not task.error:
            store.add_notification(
                task.user_id,
                "generate_done",
                {"setId": set_id, "generated": task.generated},
            )


def start(task: store.GenerateTask, set_id: str) -> None:
    """在事件循环内启动后台协程（句柄挂在任务对象上防止被 GC）。"""
    task.set_id = set_id
    task.asyncio_handle = asyncio.create_task(run_generation(task, set_id))


# ================= 引擎 B：岗位检索出题（三期，文档 3.5） =================

# 三档组织与题量占比（必备高频 / 加分项 / 差异化）
JOB_TIERS: list[tuple[str, float]] = [
    ("必备高频题", 0.5),
    ("加分项题", 0.3),
    ("差异化题", 0.2),
]

_AGG_SYSTEM = """你是岗位市场分析师。给定某岗位方向的多份真实在招 JD，请聚合分析市场共性要求。
只输出 JSON 对象，不要解释性文字、不要 Markdown 围栏：
{
  "topSkills": [{"name": "技能名", "ratio": 80}, ...]，按市场要求占比降序，共 10 项（ratio 为要求该技能的岗位百分比 0~100）；
  "duties": ["高频职责场景一句话", ...]，共 5~6 条；
  "salaryInsight": "薪资带与经验要求差异的一段话（初级 vs 资深考点侧重不同），100 字以内"；
  "tiers": {
    "core": ["必备高频考点", ...] 8~10 个，市场硬性要求；
    "bonus": ["加分项考点", ...] 5~6 个，差异化竞争力；
    "diff": ["差异化考点", ...] 4~5 个，资深岗特有或易被忽视的要求
  }
}
所有内容用中文（技术专有名词保留英文），必须仅基于给定 JD 概括，不要编造。"""

_JOB_GEN_SYSTEM = """你是资深面试官，依据某岗位方向的市场聚合要求出「单选题」。
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
- 题干须贴合给定「市场考点」的真实工作场景，避免与「已有题目」重复或高度相似；
- 不要在选项文本里加 A./B. 前缀，也不要在题干里透露答案；
- 所有内容用中文（技术专有名词保留英文）。"""


def _rule_fallback_map(keyword: str, jd_digests: list[dict[str, Any]]) -> dict[str, Any]:
    """聚合分析 LLM 失败时的规则版降级：按 JD 技能标签词频出 TOP 榜。"""
    counter: dict[str, int] = {}
    for digest in jd_digests:
        for skill in set(digest.get("skills") or []):
            counter[skill] = counter.get(skill, 0) + 1
    ranked = sorted(counter.items(), key=lambda kv: kv[1], reverse=True)[:10]
    total = max(len(jd_digests), 1)
    top = [{"name": name, "ratio": round(cnt / total * 100)} for name, cnt in ranked]
    names = [item["name"] for item in top]
    return {
        "topSkills": top,
        "duties": [],
        "salaryInsight": "",
        "tiers": {
            "core": names[:8],
            "bonus": names[8:],
            "diff": [],
        },
        "degraded": True,
        "keyword": keyword,
    }


def _jd_digest(raw: dict[str, Any]) -> dict[str, Any]:
    """全量 JD -> 聚合用摘要（描述截 600 字，控制提示词体量）。"""
    skills = raw.get("skills")
    desc = ""
    for key in ("jobDesc", "description", "content", "text", "desc"):
        value = raw.get(key)
        if value and str(value).strip():
            desc = str(value).strip()
            break
    return {
        "jobName": str(raw.get("jobName") or raw.get("name") or ""),
        "salary": str(raw.get("salary") or ""),
        "experience": str(raw.get("experience") or ""),
        "skills": [str(s) for s in skills][:8] if isinstance(skills, list) else [],
        "desc": desc[:600],
    }


async def run_job_aggregation(
    keyword: str, city: str, details: list[dict[str, Any]]
) -> dict[str, Any]:
    """JD 聚合分析（主模型一次调用）；失败时降级为规则版词频榜单（不阻断出题）。"""
    digests = [_jd_digest(d) for d in details if isinstance(d, dict)]
    digest_text = json.dumps(digests, ensure_ascii=False)
    try:
        agg = await llm.chat_json(
            "primary",
            [
                {"role": "system", "content": _AGG_SYSTEM},
                {
                    "role": "user",
                    "content": f"岗位方向：{keyword}（城市：{city or '不限'}），共 {len(digests)} 份 JD：\n{digest_text}",
                },
            ],
            temperature=0.2,
            max_tokens=8192,
            thinking=False,
        )
    except llm.LlmError:
        return _rule_fallback_map(keyword, digests)
    if not isinstance(agg, dict) or not agg.get("topSkills"):
        return _rule_fallback_map(keyword, digests)
    agg.setdefault("degraded", False)
    agg.setdefault("keyword", keyword)
    return agg


def _plan_tiers(count: int) -> list[tuple[str, int]]:
    """把题量按三档占比分配（余数归必备高频，保证档位覆盖齐全）。"""
    plan: list[tuple[str, int]] = []
    allocated = 0
    for index, (tier, ratio) in enumerate(JOB_TIERS):
        if index == len(JOB_TIERS) - 1:
            take = count - allocated
        else:
            take = round(count * ratio)
        plan.append((tier, max(take, 1)))
        allocated += take
    return plan


def _tier_points(agg: dict[str, Any], tier: str) -> list[str]:
    """该档位对应的考点清单（缺项时回退 TOP 榜前几名）。"""
    tiers = agg.get("tiers") or {}
    key_map = {"必备高频题": "core", "加分项题": "bonus", "差异化题": "diff"}
    points = tiers.get(key_map.get(tier, "core"))
    if isinstance(points, list) and points:
        return [str(p) for p in points]
    top = agg.get("topSkills") or []
    return [str(item.get("name", item)) for item in top[:4] if isinstance(item, dict) or isinstance(item, str)]


async def _generate_job_batch(
    tier: str,
    count: int,
    agg: dict[str, Any],
    avoid: list[str],
    difficulty: str,
    with_answer: bool,
    resume_context: str,
    user_id: str,
) -> list[dict[str, Any]]:
    """市场卷单批出题：按档位考点出题；难度与附答案口径由生成设置驱动。"""
    points = _tier_points(agg, tier)
    avoid_hint = ""
    if avoid:
        avoid_hint = "\n\n以下题干已存在，请勿重复或高度相似：\n" + "\n".join(f"- {s[:60]}" for s in avoid[-20:])
    if difficulty == "混合":
        difficulty_hint = "难度分布覆盖 1~3 档，其中至少 1 道为 3 档。"
    else:
        level = {"L1": 1, "L2": 2, "L3": 3}.get(difficulty, 2)
        difficulty_hint = f"全部题目 difficulty 固定为 {level} 档。"
    answer_hint = "" if with_answer else "\nreferenceAnswer 字段一律输出空字符串。"

    user_prompt = (
        f"市场聚合分析（{agg.get('keyword', '')}）：\n"
        f"TOP 技能榜：{json.dumps(agg.get('topSkills', []), ensure_ascii=False)}\n"
        f"高频职责：{'；'.join(str(d) for d in agg.get('duties', [])) or '无'}\n"
        f"薪资与经验洞察：{agg.get('salaryInsight') or '无'}\n\n"
        f"本次请生成 {count} 道「{tier}」，考点轮转覆盖：{'、'.join(points)}。"
        f"{difficulty_hint}{answer_hint}"
        + (f"\n\n候选人画像（供结合个人情况出题）：\n{resume_context}" if resume_context else "")
        + avoid_hint
    )
    items = await llm.chat_json(
        "primary",
        [
            {"role": "system", "content": _JOB_GEN_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.6,
        max_tokens=12288,
        thinking=False,
    )
    if not isinstance(items, list):
        raise llm.LlmError("出题输出不是 JSON 数组")
    normalized = [q for q in (_normalize(item, tier) for item in items) if q]
    if with_answer is False:
        for q in normalized:
            q["referenceAnswer"] = ""
    if not normalized:
        raise llm.LlmError("本批题目未通过结构校验")
    return normalized


# 两阶段漏斗（借鉴 BossHunter「关键词预筛 → JD 深度评分」）：控成本 + 产出岗位匹配分
PREFILTER_KEEP = 12  # Stage1 词法预筛保留数（减少 JD 详情抓取）
DEEP_KEEP = 10       # Stage2 深度评分后进入聚合分析的岗位数

_JOB_SCORE_SYSTEM = """你是岗位匹配度评估专家。给定候选人基准画像与若干真实在招 JD，
请逐个评估每份 JD 与候选人目标方向的相关/匹配程度。
只输出 JSON 数组，不要解释性文字、不要 Markdown 围栏：
[{"securityId": "原样回填输入的 securityId", "score": 0~100 的整数, "reason": "一句话匹配理由，30 字以内"}]
score 越高表示越贴合候选人目标岗位与能力方向；必须对每一份输入 JD 都恰好给出一条结果。"""


def _criteria_of(user_id: str, keyword: str) -> dict[str, Any]:
    """两阶段评分基准：优先简历画像（目标岗位 + 能力维度），无简历退回检索关键词。

    返回 {text: 供 LLM 的基准描述, tokens: 供 Stage1 词法预筛的小写词元集合}。
    """
    saved = store.user_resume(user_id) or {}
    analysis = saved.get("analysis") or {}
    tokens: set[str] = set()

    def _add_tokens(value: Any) -> None:
        for tok in re.split(r"[\s,，、/|+·()（）]+", str(value or "").lower()):
            tok = tok.strip()
            if len(tok) >= 2:
                tokens.add(tok)

    parts: list[str] = []
    role = analysis.get("targetRole")
    if role:
        parts.append(f"目标岗位：{role}")
        _add_tokens(role)
    dims = [d.get("label") for d in (analysis.get("dimensions") or []) if isinstance(d, dict) and d.get("label")]
    if dims:
        parts.append("能力维度：" + "、".join(str(d) for d in dims))
        for dim in dims:
            _add_tokens(dim)
    if saved.get("summary"):
        parts.append(f"画像摘要：{saved['summary']}")
        _add_tokens(saved["summary"])
    if keyword:
        parts.append(f"检索关键词：{keyword}")
        _add_tokens(keyword)
    if not parts:
        parts.append("目标岗位方向：通用技术岗")
    return {"text": "\n".join(parts), "tokens": tokens}


def _job_tokens(job: dict[str, Any]) -> set[str]:
    """岗位卡的可比对词元（岗位名 + 行业 + 技能标签），小写去噪。"""
    text = " ".join([
        str(job.get("jobName") or ""),
        str(job.get("industry") or ""),
        " ".join(str(s) for s in (job.get("skills") or [])),
    ])
    return {
        tok.strip()
        for tok in re.split(r"[\s,，、/|+·()（）]+", text.lower())
        if len(tok.strip()) >= 2
    }


def _prefilter_jobs(
    jobs: list[dict[str, Any]], criteria: dict[str, Any], keep: int = PREFILTER_KEEP
) -> list[tuple[dict[str, Any], int]]:
    """Stage1 廉价词法预筛（无 LLM）：按与评分基准的词元重叠打分排序，保留 top-K。

    返回 [(job, lexical_score 0~100)]；重叠分归一到最高者。稳定排序保留采样的薪资带分布，
    Stage2 失败时该词法分即降级匹配分。无基准词元时全部记 0 分、维持原采样序。
    """
    tokens = criteria.get("tokens") or set()
    overlaps = [(job, len(_job_tokens(job) & tokens)) for job in jobs]
    top = max((o for _, o in overlaps), default=0)
    scored = [(job, round(o / top * 100) if top else 0) for job, o in overlaps]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:keep] if keep and keep > 0 else scored


async def _score_jobs_llm(
    items: list[dict[str, Any]], criteria: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Stage2 深度相关性评分（light 模型一次批量）：返回 {securityId: {score, reason}}。

    输出不合规范 / 解析为空时抛 llm.LlmError，由调用方降级为 Stage1 词法分。
    """
    payload = [
        {
            "securityId": it.get("securityId", ""),
            "jobName": it.get("jobName", ""),
            "salary": it.get("salary", ""),
            "experience": it.get("experience", ""),
            "skills": it.get("skills", []),
            "desc": (it.get("desc") or "")[:400],
        }
        for it in items
    ]
    verdicts = await llm.chat_json(
        "light",
        [
            {"role": "system", "content": _JOB_SCORE_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"候选人基准画像：\n{criteria.get('text', '')}\n\n"
                    f"待评估 JD（{len(payload)} 份）：\n{json.dumps(payload, ensure_ascii=False)}"
                ),
            },
        ],
        temperature=0.0,
        max_tokens=4096,
        thinking=False,
    )
    if not isinstance(verdicts, list):
        raise llm.LlmError("岗位评分输出不是 JSON 数组")
    scores: dict[str, dict[str, Any]] = {}
    for verdict in verdicts:
        if not isinstance(verdict, dict):
            continue
        sid = str(verdict.get("securityId") or "").strip()
        if not sid:
            continue
        try:
            score = max(0, min(100, int(verdict.get("score"))))
        except (TypeError, ValueError):
            continue
        scores[sid] = {"score": score, "reason": str(verdict.get("reason") or "").strip()[:40]}
    if not scores:
        raise llm.LlmError("岗位评分未解析出任何有效项")
    return scores


async def run_generation_job_search(
    task: store.GenerateTask, set_id: str, ctx: dict[str, Any]
) -> None:
    """引擎 B 后台任务：取 JD 详情 -> 聚合分析（含缓存）-> 三档分批出题。

    复用引擎 A 的复判 / 去重 / 落库骨架；聚合报告独立缓存（生成设置变更只重跑出题）。
    """
    from app import boss_cli

    keyword: str = ctx["keyword"]
    city: str = ctx.get("city", "")
    difficulty: str = ctx.get("difficulty", "混合")
    with_answer: bool = bool(ctx.get("withAnswer", True))
    map_key = store.jobs_cache_key(keyword, city)
    try:
        # ① 两阶段漏斗 Stage1：廉价词法预筛（评分基准=简历画像优先，退回关键词），减少 JD 详情抓取量
        sample: list[dict[str, Any]] = (store.jobs_cache_get(map_key) or {}).get("payload") or []
        if not sample:
            task.error = "无可用岗位，请先在岗位页完成检索"
            return
        criteria = _criteria_of(task.user_id, keyword)
        prefetched = _prefilter_jobs(sample, criteria, PREFILTER_KEEP)

        # ② 仅对预筛 top-K 抓 JD 详情（缓存 7 天；逐个取，节流在 boss_cli.limiter）
        details: list[dict[str, Any]] = []
        lexical: dict[str, int] = {}
        for job, lex_score in prefetched:
            lexical[job["securityId"]] = lex_score
            cached = store.job_detail_get(job["securityId"])
            if cached is None:
                try:
                    boss_cli.limiter.wait()
                    cached = await asyncio.to_thread(boss_cli.job_detail, job["securityId"])
                    store.job_detail_put(job["securityId"], keyword, cached)
                except boss_cli.BossError as exc:
                    task.error = f"岗位详情获取失败：{exc.detail}"
                    break
            details.append({**job, **(cached if isinstance(cached, dict) else {})})
        if not details:
            task.error = task.error or "无可用岗位详情，请先在岗位页完成检索"
            return

        # ③ 聚合分析 + Stage2 深度评分（独立缓存；命中则不重跑，生成设置变更不影响缓存）
        agg = store.job_map_get(map_key)
        if agg is None:
            digests = [{**_jd_digest(d), "securityId": str(d.get("securityId") or "")} for d in details]
            try:
                llm_scores = await _score_jobs_llm(digests, criteria)
                score_degraded = False
            except llm.LlmError:
                llm_scores = {}
                score_degraded = True  # Stage2 失败降级为 Stage1 词法分，不阻断出题
            job_scores: dict[str, dict[str, Any]] = {}
            ranked: list[tuple[dict[str, Any], int]] = []
            for detail in details:
                sid = str(detail.get("securityId") or "")
                if not sid:
                    continue
                if sid in llm_scores:
                    score = int(llm_scores[sid].get("score", 0))
                    reason = str(llm_scores[sid].get("reason", ""))
                else:
                    score = lexical.get(sid, 0)
                    reason = ""
                job_scores[sid] = {"score": score, "reason": reason}
                ranked.append((detail, score))
            # 匹配分 top-M 进入聚合分析（提升考点地图质量）
            ranked.sort(key=lambda pair: pair[1], reverse=True)
            deep_details = [d for d, _ in ranked[:DEEP_KEEP]] or details
            agg = await run_job_aggregation(keyword, city, deep_details)
            agg = {**agg, "jobScores": job_scores, "scoreDegraded": score_degraded}
            store.job_map_put(keyword, city, agg)

        # ④ 三档分批出题（复用引擎 A 的复判 / 去重 / 落库与进度口径）
        plan = _plan_tiers(task.total + max(2, task.total // 4))
        primary_cfg = store.state.llm_config.get("primary") or {}
        concurrency = max(1, min(int((primary_cfg.get("params") or {}).get("concurrency", 3)), MAX_CONCURRENCY))
        semaphore = asyncio.Semaphore(concurrency)
        existing_stems = [q["stem"] for q in store.all_questions()]
        resume_context = _resume_context(task.user_id)

        async def worker(tier: str, count: int, tier_index: int) -> None:
            async with semaphore:
                with task.lock:
                    if task.generated >= task.total:
                        return
                try:
                    batch = await _generate_job_batch(
                        tier, count, agg, existing_stems, difficulty, with_answer,
                        resume_context, task.user_id,
                    )
                except llm.LlmError:
                    batch = await _generate_job_batch(
                        tier, count, agg, existing_stems, difficulty, with_answer,
                        resume_context, task.user_id,
                    )
                try:
                    verified = await _verify(batch)
                except llm.LlmError:
                    verified = await _verify(batch)
                accepted = await _dedupe(verified, existing_stems)
                with task.lock:
                    accepted = accepted[: max(task.total - task.generated, 0)]
                    if accepted:
                        store.add_questions([_to_question(item, set_id) for item in accepted], set_id)
                        existing_stems.extend(item["stem"] for item in accepted)
                        task.generated += len(accepted)
                    task.dropped += len(batch) - len(accepted)
                    task.dimension_index = tier_index
                    db.update_generate_progress(task.task_id, task.generated, task.dropped)

        results = await asyncio.gather(
            *(worker(tier, count, index) for index, (tier, count) in enumerate(plan)),
            return_exceptions=True,
        )
        failures = [r for r in results if isinstance(r, BaseException)]
        if failures:
            task.error = f"{len(failures)}/{len(plan)} 批生成失败：{failures[0]}"
        if task.generated == 0 and not task.error:
            task.error = "本次未产出任何题目（全部被去重或校验淘汰），请重试"
    except Exception as exc:
        task.error = f"出题任务异常：{exc}"
    finally:
        with task.lock:
            task.done = True
        db.finish_generate_task(task.task_id, task.error)
        if not task.error:
            store.add_notification(
                task.user_id,
                "generate_done",
                {"setId": set_id, "generated": task.generated},
            )


def start_job_search(task: store.GenerateTask, set_id: str, ctx: dict[str, Any]) -> None:
    """启动引擎 B 后台任务（句柄挂在任务对象上防止被 GC）。"""
    task.set_id = set_id
    task.asyncio_handle = asyncio.create_task(run_generation_job_search(task, set_id, ctx))


# ========= 引擎 C（子集）：单岗位专属预测题（四期增补，source=jd_target） =========

_JD_GEN_SYSTEM = """你是资深面试官，依据「单个真实在招岗位的 JD」预测该岗位面试最可能问的问题，出「单选题」。
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
- 题干须紧扣给定 JD 的职责与要求（其技术栈/业务场景），预测该岗位真实面试会问的点，避免与「已有题目」重复或高度相似；
- 不要在选项文本里加 A./B. 前缀，也不要在题干里透露答案；
- 所有内容用中文（技术专有名词保留英文）。"""


def _jd_context_text(jd: dict[str, Any]) -> str:
    """单 JD 上下文文本（注入出题提示词）：岗位名/公司/城市/技能/描述截断。"""
    return (
        f"岗位：{jd.get('jobName') or '未知'}\n"
        f"公司：{jd.get('brand') or '未知'}（城市：{jd.get('city') or '不限'}）\n"
        f"技能要求：{'、'.join(str(s) for s in (jd.get('skills') or [])) or '无'}\n"
        f"职责与要求描述：{str(jd.get('desc') or '')[:800] or '无'}"
    )


async def _generate_jd_batch(
    dimension: str,
    count: int,
    jd: dict[str, Any],
    avoid: list[str],
    difficulty: str,
    with_answer: bool,
    resume_context: str,
    user_id: str,
) -> list[dict[str, Any]]:
    """单岗位专属单批出题：紧扣该 JD 要求、按维度预测面试问题。"""
    avoid_hint = ""
    if avoid:
        avoid_hint = "\n\n以下题干已存在，请勿重复或高度相似：\n" + "\n".join(f"- {s[:60]}" for s in avoid[-20:])
    if difficulty == "混合":
        difficulty_hint = "难度分布覆盖 1~3 档，其中至少 1 道为 3 档。"
    else:
        level = {"L1": 1, "L2": 2, "L3": 3}.get(difficulty, 2)
        difficulty_hint = f"全部题目 difficulty 固定为 {level} 档。"
    answer_hint = "" if with_answer else "\nreferenceAnswer 字段一律输出空字符串。"

    user_prompt = (
        f"目标岗位 JD：\n{_jd_context_text(jd)}\n\n"
        f"本次请生成 {count} 道题，全部聚焦「{dimension}」这一维度，"
        f"紧扣上述 JD 的职责与要求预测该岗位面试问题。"
        f"{difficulty_hint}{answer_hint}"
        + (f"\n\n候选人画像（供结合个人情况出题）：\n{resume_context}" if resume_context else "")
        + avoid_hint
    )
    items = await llm.chat_json(
        "primary",
        [
            {"role": "system", "content": _JD_GEN_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.6,
        max_tokens=12288,
        thinking=False,
    )
    if not isinstance(items, list):
        raise llm.LlmError("出题输出不是 JSON 数组")
    normalized = [q for q in (_normalize(item, dimension) for item in items) if q]
    if with_answer is False:
        for q in normalized:
            q["referenceAnswer"] = ""
    if not normalized:
        raise llm.LlmError("本批题目未通过结构校验")
    return normalized


async def run_generation_job_detail(
    task: store.GenerateTask, set_id: str, ctx: dict[str, Any]
) -> None:
    """引擎 C（子集）后台任务：取单岗位 JD -> 六维规划分批出专属预测题。

    复用引擎 A/B 的复判 / 去重 / 落库与进度口径；JD 走 detail 缓存（7 天）。
    """
    from app import boss_cli

    security_id: str = ctx["securityId"]
    difficulty: str = ctx.get("difficulty", "混合")
    with_answer: bool = bool(ctx.get("withAnswer", True))
    try:
        # ① 取单岗位 JD（缓存 7 天；未命中则节流抓取）
        detail = store.job_detail_get(security_id)
        if detail is None:
            try:
                boss_cli.limiter.wait()
                detail = await asyncio.to_thread(boss_cli.job_detail, security_id)
                store.job_detail_put(security_id, str(ctx.get("keyword") or ""), detail)
            except boss_cli.BossError as exc:
                task.error = f"岗位详情获取失败：{exc.detail}"
                return
        if not isinstance(detail, dict):
            task.error = "岗位详情解析失败，请稍后重试"
            return
        jd = {
            **_jd_digest(detail),
            "brand": str(detail.get("brandName") or detail.get("brand") or detail.get("companyName") or ""),
            "city": str(detail.get("cityName") or detail.get("city") or ""),
        }
        if ctx.get("jobName"):
            jd["jobName"] = str(ctx["jobName"])

        # ② 六维规划（同引擎 A 覆盖矩阵）+ 并发/复判/去重/落库口径同引擎 B
        plan = _plan(task.total + max(2, task.total // 4))
        primary_cfg = store.state.llm_config.get("primary") or {}
        concurrency = max(1, min(int((primary_cfg.get("params") or {}).get("concurrency", 3)), MAX_CONCURRENCY))
        semaphore = asyncio.Semaphore(concurrency)
        existing_stems = [q["stem"] for q in store.all_questions()]
        resume_context = _resume_context(task.user_id)

        async def worker(dimension: str, count: int, dim_index: int) -> None:
            async with semaphore:
                with task.lock:
                    if task.generated >= task.total:
                        return
                try:
                    batch = await _generate_jd_batch(
                        dimension, count, jd, existing_stems, difficulty, with_answer,
                        resume_context, task.user_id,
                    )
                except llm.LlmError:
                    batch = await _generate_jd_batch(
                        dimension, count, jd, existing_stems, difficulty, with_answer,
                        resume_context, task.user_id,
                    )
                try:
                    verified = await _verify(batch)
                except llm.LlmError:
                    verified = await _verify(batch)
                accepted = await _dedupe(verified, existing_stems)
                with task.lock:
                    accepted = accepted[: max(task.total - task.generated, 0)]
                    if accepted:
                        store.add_questions([_to_question(item, set_id) for item in accepted], set_id)
                        existing_stems.extend(item["stem"] for item in accepted)
                        task.generated += len(accepted)
                    task.dropped += len(batch) - len(accepted)
                    task.dimension_index = dim_index
                    db.update_generate_progress(task.task_id, task.generated, task.dropped)

        results = await asyncio.gather(
            *(worker(dim, count, index) for index, (dim, count) in enumerate(plan)),
            return_exceptions=True,
        )
        failures = [r for r in results if isinstance(r, BaseException)]
        if failures:
            task.error = f"{len(failures)}/{len(plan)} 批生成失败：{failures[0]}"
        if task.generated == 0 and not task.error:
            task.error = "本次未产出任何题目（全部被去重或校验淘汰），请重试"
    except Exception as exc:
        task.error = f"出题任务异常：{exc}"
    finally:
        with task.lock:
            task.done = True
        db.finish_generate_task(task.task_id, task.error)
        if not task.error:
            store.add_notification(
                task.user_id,
                "generate_done",
                {"setId": set_id, "generated": task.generated},
            )


def start_job_detail(task: store.GenerateTask, set_id: str, ctx: dict[str, Any]) -> None:
    """启动单岗位专属出题后台任务（句柄挂在任务对象上防止被 GC）。"""
    task.set_id = set_id
    task.asyncio_handle = asyncio.create_task(run_generation_job_detail(task, set_id, ctx))
