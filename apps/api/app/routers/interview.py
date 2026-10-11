"""语音模拟面试（五期，文档 3.9）：动态出题、TTS 读题、ASR 转写作答、
light 层即时评分（STAR 四维 + 命中关键词）、动态追问（上限 2 轮）、primary 层复盘报告、录音回放。

链路（对齐已批准方案）：
- 出题：POST "" 按「目标岗位 + 简历」实时动态生成开放式题（generation.generate_interview_questions），
  以 JSON 快照存于会话（不入题库）；
- 读题：POST /{id}/tts 走云端 Qwen3-TTS（tts.asynthesize），同源回传可播放 WAV；
- 作答：POST /{id}/answers 上传 wav → 本地 SenseVoice 离线转写（asr.atranscribe）→ light 层评分；
  无麦克风 / 弱网 / ASR 未就绪时前端降级为文字输入（只传 transcript，不传 file）；
- 追问：七期升级为 STAR 评分驱动的动态追问循环（上限 2 轮）——按四维评分决策
  「是否追问/追问方向」，LLM 失败回落题目预置追问；作答/追问轮次与决策轨迹入 meta；
- 复盘：POST /{id}/finish 走 primary 层生成四维雷达 + 总评 + 改进建议（含追问质量分析），
  GET /{id}/report 读取。

合规口径（文档第五章第 6 条）：
- 麦克风始终用于 ASR（语音面试前提），音频上传后端离线转写；
- 录音默认不留存，转写后即弃；仅「逐场显式开启」（session.recordAudio）且本次 retainAudio 才落盘；
- 录音回放 / 删除接口仅本人可访问；随会话删除（DELETE）/ 账号注销（db.clear_user_data）一并清除。
"""

import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from app import agent_runtime, asr, generation, llm, store, tts
from app.auth import get_current_user
from app.config import settings
from app.schemas import InterviewSessionCreate, InterviewTtsRequest

router = APIRouter(prefix="/api/interview-sessions", tags=["interview"])

TIME_FMT = "%Y-%m-%d %H:%M:%S"
MAX_AUDIO_MB = 25         # 单段录音上限（16k 单声道 16bit ≈ 12 分钟），防御异常大文件
FOLLOWUP_MIN_CHARS = 20   # 回答短于此不触发追问（无实质内容，追问无意义）
SCORE_KEYS = ("situation", "task", "action", "result")


def _now() -> str:
    return datetime.now().strftime(TIME_FMT)


def _clamp_score(value: object) -> int:
    try:
        return max(0, min(10, int(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _recordings_root() -> Path:
    return Path(settings.recordings_dir).resolve()


def _require_session(user_id: str, session_id: str) -> dict:
    session = store.interview_session(user_id, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="面试会话不存在")
    return session


def _question_at(session: dict, seq: int) -> dict:
    questions = session.get("questions") or []
    if seq < 0 or seq >= len(questions):
        raise HTTPException(status_code=400, detail="题序超出范围")
    return questions[seq]


def _tts_ready() -> bool:
    """voice 层是否可用于合成（已启用 + 配好 Key）；供前端决定是否展示读题按钮。"""
    try:
        cfg = llm.layer_config("voice")
    except Exception:
        return False
    return bool(cfg.get("apiKey")) and cfg.get("enabled", True)


# ---------------- 录音落盘 / 清理（仅开启留存时） ----------------


def _save_recording(session_id: str, name: str, wav_bytes: bytes) -> str:
    """落盘录音到 recordings/{session_id}/{name}.wav，返回相对路径；失败返回空串（不阻断作答）。"""
    try:
        root = _recordings_root()
        folder = root / session_id
        folder.mkdir(parents=True, exist_ok=True)
        rel = f"{session_id}/{name}.wav"
        (root / rel).write_bytes(wav_bytes)
        return rel
    except Exception:
        return ""


def _delete_recordings(session_id: str) -> None:
    """删除某会话的录音目录（防目录穿越：解析后必须仍在 recordings 根内）。"""
    root = _recordings_root()
    folder = (root / session_id).resolve()
    if folder.exists() and str(folder).startswith(str(root)):
        shutil.rmtree(folder, ignore_errors=True)


# ---------------- 响应整形 ----------------


def _answer_payload(a: dict) -> dict:
    return {
        "id": a["id"],
        "sessionId": a.get("sessionId", ""),
        "seq": a.get("seq", 0),
        "question": a.get("question", {}),
        "transcript": a.get("transcript", ""),
        "starScores": a.get("starScores"),
        "hitKeywords": a.get("hitKeywords", []),
        "missedKeywords": a.get("missedKeywords", []),
        "comment": a.get("comment", ""),
        "durationSec": a.get("durationSec", 0),
        "hasRecording": bool(a.get("recordingPath")),
    }


def _session_payload(session: dict, answers: list[dict] | None = None, current_seq: int | None = None) -> dict:
    questions = session.get("questions") or []
    answered = answers if answers is not None else session.get("answers", [])
    if current_seq is None:
        current_seq = min(len(answered), max(len(questions) - 1, 0))
    return {
        "id": session["id"],
        "mode": session.get("mode", "mixed"),
        "targetJob": session.get("targetJob", ""),
        "questions": questions,
        "status": session.get("status", "running"),
        "recordAudio": bool(session.get("recordAudio")),
        "startedAt": session.get("startedAt", ""),
        "finishedAt": session.get("finishedAt"),
        "total": len(questions),
        "answered": len(answered),
        "currentSeq": current_seq,
        "report": session.get("report"),
        "answers": [_answer_payload(a) for a in sorted(answered, key=lambda x: x.get("seq", 0))],
    }


def _report_payload(session: dict, answers: list[dict]) -> dict:
    """复盘报告响应：会话概要 + 报告 + 每题文字稿回放（含追问轮次）。"""
    detailed = []
    for a in sorted(answers, key=lambda x: x.get("seq", 0)):
        item = _answer_payload(a)
        item["turns"] = [
            {
                "turnNo": t.get("turnNo"),
                "role": t.get("role"),
                "transcript": t.get("transcript", ""),
                "meta": t.get("meta"),
            }
            for t in store.interview_turns(a["id"])
        ]
        detailed.append(item)
    return {
        "id": session["id"],
        "mode": session.get("mode", "mixed"),
        "targetJob": session.get("targetJob", ""),
        "status": session.get("status", "running"),
        "total": len(session.get("questions") or []),
        "answered": len(answers),
        "startedAt": session.get("startedAt", ""),
        "finishedAt": session.get("finishedAt"),
        "report": session.get("report"),
        "answers": detailed,
    }


# ---------------- light 层即时评分（STAR 四维 + 命中关键词） ----------------

_SCORE_SYSTEM = """你是面试评分助手。给定面试题、参考关键词与候选人回答（语音转写文字稿），评估回答质量。
只输出 JSON 对象，不要解释性文字、不要 Markdown 围栏：
{
  "starScores": {"situation": 0, "task": 0, "action": 0, "result": 0},
  "hitKeywords": ["从参考关键词中原样复制、回答确实覆盖的项"],
  "comment": "一句话点评，50 字内，指出亮点与主要不足"
}
评分口径（各维 0~10 整数）：
- STAR 题：situation=背景交代、task=任务目标、action=行动与技术细节、result=结果与量化成效；
- 技术/HR 题按同一四维映射：situation=问题理解、task=方案/观点目标、action=论述与技术细节、result=结论与成效；
- hitKeywords 必须从给定「参考关键词」中原样复制命中的项（语义覆盖即可），不要新增或改写；
- 回答为空、过短或答非所问时各维给 0~3 分。"""


def _heuristic_score(question: dict, transcript: str) -> dict:
    """light 层不可用时的规则兜底：关键词子串命中 + 按篇幅粗估四维（不给高分）。"""
    keywords = question.get("keywords") or []
    low = (transcript or "").lower()
    hit = [k for k in keywords if k and k.lower() in low]
    missed = [k for k in keywords if k not in hit]
    base = max(0, min(6, len(transcript or "") // 40))
    return {
        "starScores": {key: base for key in SCORE_KEYS},
        "hitKeywords": hit,
        "missedKeywords": missed,
        "comment": "（轻量评分模型暂不可用，按关键词命中与作答篇幅粗估）",
    }


async def _score_answer(question: dict, transcript: str) -> dict:
    """light 层即时评分（3s 内）；LLM 失败降级规则兜底，绝不 500。"""
    keywords = question.get("keywords") or []
    if not (transcript or "").strip():
        return {
            "starScores": {key: 0 for key in SCORE_KEYS},
            "hitKeywords": [],
            "missedKeywords": list(keywords),
            "comment": "未作答或转写为空。",
        }
    user_prompt = (
        f"面试题（{question.get('dimension', '技术')}）：{question.get('stem', '')}\n"
        f"参考关键词：{keywords}\n"
        f"候选人回答（ASR 文字稿）：{transcript}\n\n"
        "请按要求输出评分 JSON。"
    )
    try:
        data = await llm.chat_json(
            "light",
            [
                {"role": "system", "content": _SCORE_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=1024,
            thinking=False,
        )
    except llm.LlmError:
        return _heuristic_score(question, transcript)
    if not isinstance(data, dict):
        return _heuristic_score(question, transcript)

    raw_scores = data.get("starScores")
    scores = (
        {key: _clamp_score(raw_scores.get(key)) for key in SCORE_KEYS}
        if isinstance(raw_scores, dict)
        else {key: 0 for key in SCORE_KEYS}
    )
    # 命中关键词以参考列表为准（防模型幻觉/改写）：仅保留确实在参考列表中的项
    canonical = list(keywords)
    raw_hit = data.get("hitKeywords") if isinstance(data.get("hitKeywords"), list) else []
    hit = [k for k in canonical if any(str(h).strip() == k for h in raw_hit)]
    # 兜底：模型漏判但文字稿字面命中的补回
    low = transcript.lower()
    for k in canonical:
        if k not in hit and k and k.lower() in low:
            hit.append(k)
    missed = [k for k in canonical if k not in hit]
    comment = str(data.get("comment") or "").strip()[:200]
    return {"starScores": scores, "hitKeywords": hit, "missedKeywords": missed, "comment": comment}


# ---------------- 动态追问循环（七期：STAR 评分驱动，上限 2 轮） ----------------

FOLLOWUP_MAX_ROUNDS = 2  # 追问轮数硬上限：达到即强制停止，防止无限对话
_FOLLOWUP_SYSTEM = """你是资深面试官。基于候选人上一轮回答与其 STAR 四维评分，判断是否需要追问以澄清薄弱环节。
只输出 JSON 对象，不要解释性文字、不要 Markdown 围栏：
{"ask": true, "followup": "追问问题，一句话 40 字以内，聚焦最薄弱的维度", "target": "situation/task/action/result 之一", "reason": "一句话决策依据，30 字以内"}
评分口径：四维均衡且均 >= 7 分视为已充分，可不追问（ask=false）；追问必须针对最低分维度深挖，不要重复上一问。"""

# LLM 失败时的维度兜底追问模板（第 2 轮起没有预置追问可用，按最弱维度取模板）
_PRESET_FOLLOWUPS = {
    "situation": "能补充一下当时的业务背景和你的具体角色吗？",
    "task": "这个任务的目标和验收标准具体是什么？",
    "action": "具体讲讲你个人负责的关键动作和技术细节？",
    "result": "最终结果如何？有可量化的数据或复盘结论吗？",
}


def _star_average(star: dict) -> float:
    """STAR 四维均分（0~10）。"""
    vals = [_clamp_score((star or {}).get(k)) for k in SCORE_KEYS]
    return sum(vals) / len(SCORE_KEYS)


def _weakest_dimension(star: dict) -> str:
    """最薄弱维度：分值升序；并列时优先 action（追问行动细节信息量最大）。"""
    return min(SCORE_KEYS, key=lambda k: (_clamp_score((star or {}).get(k)), k != "action"))


async def _decide_followup(question: dict, transcript: str, scored: dict, round_no: int) -> tuple[str, dict]:
    """STAR 评分驱动的追问决策（七期 Agent 化）：light 层判定「是否追问/追问方向」。

    返回 (追问文本, 决策 meta)；判定不追问时文本为空串。
    LLM 失败静默降级：第 1 轮回落题目预置 followUp（保五期行为），
    之后按最弱维度取兜底模板（避免重复同一预置问句）。
    """
    star = scored.get("starScores") or {}
    weakest = _weakest_dimension(star)
    preset = str(question.get("followUp") or "").strip()
    meta: dict = {"round": round_no, "dynamic": False, "target": weakest, "presetUsed": False}

    run = agent_runtime.AgentRun(f"followup:r{round_no}", ctx={"weakest": weakest})

    async def _decide(r: agent_runtime.AgentRun) -> str:
        scores = {k: _clamp_score(star.get(k)) for k in SCORE_KEYS}
        user_prompt = (
            f"面试题（{question.get('dimension', '技术')}）：{question.get('stem', '')}\n"
            f"候选人回答：{transcript[:600]}\n"
            f"STAR 四维评分（0~10）：{scores}\n\n"
            f"这是第 {round_no} 轮追问的决策。请按要求输出 JSON。"
        )
        data = await llm.chat_json(
            "light",
            [
                {"role": "system", "content": _FOLLOWUP_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
            max_tokens=512,
            thinking=False,
        )
        if not isinstance(data, dict):
            raise llm.LlmError("追问决策未返回 JSON 对象")
        r.ctx["decision"] = data
        return str(data.get("followup") or "").strip()

    record = await run.astep("llm-decide", _decide)
    meta["steps"] = run.summary()["trajectory"]

    decision = run.ctx.get("decision") if record is not None and record.ok else None
    if isinstance(decision, dict):
        meta["dynamic"] = True
        meta["reason"] = str(decision.get("reason") or "").strip()[:80]
        target = str(decision.get("target") or "").strip()
        if target in SCORE_KEYS:
            meta["target"] = target
        if decision.get("ask"):
            followup = str(decision.get("followup") or "").strip()[:120]
            if followup:
                return followup, meta
        return "", meta  # 判定回答已充分（ask=false）→ 尊重决策，不追问

    # LLM 失败 / 未产出有效决策 → 回落预置追问（静默降级，绝不 500）
    meta["presetUsed"] = True
    fallback = preset if round_no <= 1 and preset else _PRESET_FOLLOWUPS[weakest]
    return fallback, meta


# ---------------- primary 层复盘报告 ----------------

_REPORT_SYSTEM = """你是资深面试官，请基于一场模拟面试的全部问答给出复盘总评。
只输出 JSON 对象，不要解释性文字、不要 Markdown 围栏：
{
  "overall": 0~100 的整数综合得分,
  "summary": "一段话总评，100 字内，客观指出整体表现",
  "strengths": ["亮点，最多 3 条，每条 30 字内"],
  "improvements": ["改进建议，3~5 条，每条具体可操作，40 字内"]
}
所有内容用中文。"""


def _followup_analysis(answers: list[dict]) -> dict:
    """追问质量分析（七期）：各作答的追问轮数、动态决策占比、末轮评分提升。"""
    items: list[dict] = []
    total_rounds = 0
    dynamic_rounds = 0
    for a in sorted(answers, key=lambda x: x.get("seq", 0)):
        turns = store.interview_turns(a["id"])
        asked = [t for t in turns if t.get("role") == "interviewer"]
        rounds = len(asked)
        dynamic = sum(1 for t in asked if (t.get("meta") or {}).get("dynamic"))
        base_avg = _star_average(a.get("starScores") or {})
        candidates = [t for t in turns if t.get("role") == "candidate"]
        last_star = (candidates[-1].get("meta") or {}).get("starScores") if candidates else None
        last_avg = _star_average(last_star) if isinstance(last_star, dict) else base_avg
        items.append({
            "seq": a.get("seq", 0),
            "rounds": rounds,
            "dynamic": dynamic,
            "baseAverage": round(base_avg, 1),
            "lastAverage": round(last_avg, 1),
            "improved": last_avg > base_avg,
        })
        total_rounds += rounds
        dynamic_rounds += dynamic
    return {
        "answersWithFollowup": sum(1 for i in items if i["rounds"] > 0),
        "totalRounds": total_rounds,
        "dynamicRounds": dynamic_rounds,
        "improved": sum(1 for i in items if i["improved"]),
        "items": items,
    }


async def _build_report(session: dict, answers: list[dict]) -> dict:
    """四维雷达（各题平均，规则计算）+ primary 层总评/优势/改进；LLM 失败降级规则汇总。"""
    scored = [a for a in answers if isinstance(a.get("starScores"), dict)]
    radar = {key: 0 for key in SCORE_KEYS}
    if scored:
        for key in SCORE_KEYS:
            vals = [_clamp_score((a["starScores"] or {}).get(key)) for a in scored]
            radar[key] = int(round(sum(vals) / len(vals)))

    qa_lines = []
    for a in sorted(scored, key=lambda x: x.get("seq", 0)):
        q = a.get("question") or {}
        s = a.get("starScores") or {}
        dims = "/".join(str(s.get(k, 0)) for k in SCORE_KEYS)
        qa_lines.append(
            f"{len(qa_lines) + 1}. [{q.get('dimension', '技术')}] {q.get('stem', '')}\n"
            f"   回答：{(a.get('transcript') or '')[:300]}\n   四维(S/T/A/R)：{dims}"
        )
    user_prompt = (
        f"目标岗位：{session.get('targetJob') or '未指定'}\n"
        f"本场共 {len(answers)} 题，其中 {len(scored)} 题有效作答。\n\n"
        + ("\n".join(qa_lines) if qa_lines else "（无有效作答）")
        + "\n\n请给出复盘总评 JSON。"
    )
    try:
        data = await llm.chat_json(
            "primary",
            [
                {"role": "system", "content": _REPORT_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
            max_tokens=4096,
            thinking=False,
        )
    except llm.LlmError:
        data = {}
    if not isinstance(data, dict):
        data = {}

    try:
        overall = max(0, min(100, int(data.get("overall"))))
    except (TypeError, ValueError):
        overall = int(round(sum(radar.values()) / len(SCORE_KEYS) * 10)) if scored else 0

    def _str_list(value: object, limit: int, width: int) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(v).strip()[:width] for v in value if str(v or "").strip()][:limit]

    summary = str(data.get("summary") or "").strip()[:300]
    if not summary:
        summary = (
            f"本场共作答 {len(scored)} 题，综合得分 {overall}。"
            if scored else "本场未产生有效作答，暂无可评估内容。"
        )
    return {
        "overall": overall,
        "radar": radar,
        "summary": summary,
        "strengths": _str_list(data.get("strengths"), 3, 60),
        "improvements": _str_list(data.get("improvements"), 5, 80),
        "followup": _followup_analysis(answers),
    }


# ---------------- 端点 ----------------


@router.post("", status_code=201)
async def create_session(body: InterviewSessionCreate, user_id: str = Depends(get_current_user)) -> dict:
    """创建面试会话：按「目标岗位 + 简历」实时动态出题（不落题库），返回整场题目与首题。"""
    profile = store.user_profile(user_id)
    target_job = (body.targetJob or "").strip() or profile.get("targetRole") or ""
    try:
        questions = await generation.generate_interview_questions(
            body.mode, body.count, target_job, user_id
        )
    except llm.LlmError as exc:
        raise HTTPException(status_code=502, detail=f"面试题生成失败：{exc}") from exc
    if not questions:
        raise HTTPException(status_code=502, detail="面试题生成失败：未产出有效题目，请重试")

    session = {
        "id": store.new_id("itv"),
        "userId": user_id,
        "mode": body.mode,
        "targetJob": target_job,
        "questions": questions,
        "status": "running",
        "recordAudio": bool(body.recordAudio),
        "report": None,
        "startedAt": _now(),
        "finishedAt": None,
        "answers": [],
    }
    store.persist_interview_session(session)
    return _session_payload(session, current_seq=0)


@router.get("")
def list_sessions(user_id: str = Depends(get_current_user)) -> list[dict]:
    """历史面试列表（倒序，供复盘/继续入口）。"""
    return [
        {
            "id": s["id"],
            "mode": s.get("mode", "mixed"),
            "targetJob": s.get("targetJob", ""),
            "total": len(s.get("questions") or []),
            "status": s.get("status", "running"),
            "startedAt": s.get("startedAt", ""),
            "finishedAt": s.get("finishedAt"),
            "hasReport": s.get("report") is not None,
        }
        for s in store.user_interview_sessions(user_id)
    ]


@router.get("/capabilities")
def capabilities(user_id: str = Depends(get_current_user)) -> dict:
    """语音能力探测：前端据此决定走录音还是降级文字输入、是否展示读题。"""
    return {"asrAvailable": asr.is_available(), "ttsAvailable": _tts_ready()}


@router.get("/{session_id}")
def get_session(session_id: str, user_id: str = Depends(get_current_user)) -> dict:
    """取会话进度与题目（含已答明细，支持刷新/断网恢复）。"""
    session = _require_session(user_id, session_id)
    return _session_payload(session, answers=store.interview_answers(session_id))


@router.post("/{session_id}/tts")
async def synthesize_question(
    session_id: str, body: InterviewTtsRequest, user_id: str = Depends(get_current_user)
) -> Response:
    """题目/追问文字 → 面试官语音（云端 Qwen3-TTS），同源回传可播放 WAV。"""
    session = _require_session(user_id, session_id)
    speak = (body.text or "").strip()
    if not speak:
        speak = str(_question_at(session, body.seq).get("stem", "")).strip()
    if not speak:
        raise HTTPException(status_code=400, detail="没有可合成的文本")
    try:
        result = await tts.asynthesize(speak)
    except (tts.TtsError, llm.LlmError) as exc:
        raise HTTPException(status_code=502, detail=f"语音合成失败：{exc}") from exc
    return Response(content=result["audio"], media_type=result.get("contentType", "audio/wav"))


@router.post("/{session_id}/answers")
async def submit_answer(
    session_id: str,
    seq: int = Form(...),
    file: UploadFile | None = File(None),
    transcript: str = Form(""),
    durationSec: int = Form(0),
    retainAudio: bool = Form(True),
    user_id: str = Depends(get_current_user),
) -> dict:
    """提交单题作答：上传 wav 走本地 ASR 转写（或直接传 transcript 文字降级）→
    light 层 STAR 四维 + 命中关键词即时评分 → STAR 评分决策是否触发动态追问（最多 2 轮）；
    仅「逐场显式开启」且本次 retainAudio 才落盘录音（默认不留存）。重新作答按 seq 覆盖。"""
    session = _require_session(user_id, session_id)
    if session.get("status") != "running":
        raise HTTPException(status_code=409, detail="面试已结束，无法继续作答")
    question = _question_at(session, seq)

    wav_bytes = await file.read() if file is not None else b""
    if len(wav_bytes) > MAX_AUDIO_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"录音超过 {MAX_AUDIO_MB}MB 上限")

    if wav_bytes:
        if not asr.is_available():
            raise HTTPException(status_code=422, detail="本地语音识别未就绪（权重未下载），请改用文字输入")
        try:
            result = await asr.atranscribe(wav_bytes)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"语音转写失败：{exc}") from exc
        text = (result.get("text") or "").strip()
        duration = int(durationSec or result.get("durationSec") or 0)
    else:
        text = (transcript or "").strip()
        duration = int(durationSec or 0)

    scored = await _score_answer(question, text)

    # 重新作答复用同 id（覆盖写，避免同 seq 产生重复行）
    existing = next((a for a in store.interview_answers(session_id) if a.get("seq") == seq), None)
    answer_id = existing["id"] if existing else store.new_id("itva")

    recording_path = ""
    if wav_bytes and session.get("recordAudio") and retainAudio:
        recording_path = _save_recording(session_id, answer_id, wav_bytes)

    answer = {
        "id": answer_id,
        "sessionId": session_id,
        "seq": seq,
        "question": question,
        "transcript": text,
        "starScores": scored["starScores"],
        "hitKeywords": scored["hitKeywords"],
        "missedKeywords": scored["missedKeywords"],
        "comment": scored["comment"],
        "durationSec": duration,
        "recordingPath": recording_path or (existing or {}).get("recordingPath", ""),
    }
    store.persist_interview_answer(answer)

    # 动态追问判定（七期）：回答有实质内容 → STAR 评分决策「是否追问/追问方向」，
    # 追问以确定性 id 幂等落库，决策轨迹入轮次 meta
    follow_up = ""
    if len(text) >= FOLLOWUP_MIN_CHARS:
        follow_up, follow_meta = await _decide_followup(question, text, scored, round_no=1)
        if follow_up:
            store.persist_interview_turn({
                "id": f"{answer_id}-t1", "answerId": answer_id, "turnNo": 1,
                "role": "interviewer", "transcript": follow_up, "meta": follow_meta,
            })

    payload = _answer_payload(answer)
    payload["followUp"] = follow_up or None
    return payload


@router.post("/{session_id}/answers/{answer_id}/followup")
async def submit_followup(
    session_id: str,
    answer_id: str,
    file: UploadFile | None = File(None),
    transcript: str = Form(""),
    durationSec: int = Form(0),
    retainAudio: bool = Form(True),
    user_id: str = Depends(get_current_user),
) -> dict:
    """提交追问轮的作答：定位最近一个待回答的面试官追问，回答其下一轮
    （turnNo 递增、确定性 id 幂等，重试覆盖同轮）；评分入轮次 meta。
    未达追问上限且回答有实质内容时，决策是否继续追问下一轮。"""
    session = _require_session(user_id, session_id)
    if session.get("status") != "running":
        raise HTTPException(status_code=409, detail="面试已结束")
    answer = next((a for a in store.interview_answers(session_id) if a["id"] == answer_id), None)
    if answer is None:
        raise HTTPException(status_code=404, detail="作答不存在")

    wav_bytes = await file.read() if file is not None else b""
    if len(wav_bytes) > MAX_AUDIO_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"录音超过 {MAX_AUDIO_MB}MB 上限")
    if wav_bytes:
        if not asr.is_available():
            raise HTTPException(status_code=422, detail="本地语音识别未就绪（权重未下载），请改用文字输入")
        try:
            result = await asr.atranscribe(wav_bytes)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"语音转写失败：{exc}") from exc
        text = (result.get("text") or "").strip()
    else:
        text = (transcript or "").strip()

    # 定位最近一个待回答的面试官追问：回答其 turnNo+1（id 确定性，重试覆盖同轮）
    turns = store.interview_turns(answer_id)
    interviewer_turns = [t for t in turns if t.get("role") == "interviewer"]
    if not interviewer_turns:
        raise HTTPException(status_code=409, detail="该作答当前没有待回答的追问")
    last = max(interviewer_turns, key=lambda t: t.get("turnNo", 0))
    reply_turn_no = int(last.get("turnNo", 0)) + 1

    # 候选人轮次带评分 meta（复盘报告据此计算追问带来的提升）
    question = answer.get("question") or {}
    scored = await _score_answer(question, text)
    store.persist_interview_turn({
        "id": f"{answer_id}-t{reply_turn_no}", "answerId": answer_id,
        "turnNo": reply_turn_no, "role": "candidate", "transcript": text,
        "meta": {"starScores": scored["starScores"], "comment": scored["comment"]},
    })
    if wav_bytes and session.get("recordAudio") and retainAudio:
        _save_recording(session_id, f"{answer_id}-fu{reply_turn_no}", wav_bytes)

    # 未达追问上限 → STAR 决策是否继续追问下一轮（达到上限强制停止）
    rounds_asked = len(interviewer_turns)
    next_followup = ""
    if rounds_asked < FOLLOWUP_MAX_ROUNDS and len(text) >= FOLLOWUP_MIN_CHARS:
        next_followup, next_meta = await _decide_followup(
            question, text, scored, round_no=rounds_asked + 1
        )
        if next_followup:
            store.persist_interview_turn({
                "id": f"{answer_id}-t{reply_turn_no + 1}", "answerId": answer_id,
                "turnNo": reply_turn_no + 1, "role": "interviewer",
                "transcript": next_followup, "meta": next_meta,
            })
            rounds_asked += 1

    return {
        "ok": True,
        "seq": answer.get("seq", 0),
        "transcript": text,
        "starScores": scored["starScores"],
        "followUp": next_followup or None,
        "rounds": rounds_asked,
    }


@router.post("/{session_id}/finish")
async def finish_session(session_id: str, user_id: str = Depends(get_current_user)) -> dict:
    """结束面试：primary 层生成复盘报告（四维雷达 + 总评 + 改进建议）。已结束则直接回报告。"""
    session = _require_session(user_id, session_id)
    answers = store.interview_answers(session_id)
    if session.get("status") == "finished" and session.get("report"):
        return _report_payload(session, answers)
    session["report"] = await _build_report(session, answers)
    session["status"] = "finished"
    session["finishedAt"] = _now()
    store.persist_interview_session(session)
    return _report_payload(session, answers)


@router.get("/{session_id}/report")
def get_report(session_id: str, user_id: str = Depends(get_current_user)) -> dict:
    """读复盘报告（含每题文字稿回放与追问轮次）。"""
    session = _require_session(user_id, session_id)
    if session.get("status") != "finished" or not session.get("report"):
        raise HTTPException(status_code=409, detail="面试尚未结束或报告未生成")
    return _report_payload(session, store.interview_answers(session_id))


@router.get("/{session_id}/answers/{answer_id}/audio")
def get_answer_audio(session_id: str, answer_id: str, user_id: str = Depends(get_current_user)) -> FileResponse:
    """录音回放：仅本人、仅开启留存时；文件缺失返回 404。"""
    session = _require_session(user_id, session_id)
    if not session.get("recordAudio"):
        raise HTTPException(status_code=403, detail="本场未开启录音留存")
    answer = next((a for a in store.interview_answers(session_id) if a["id"] == answer_id), None)
    if answer is None or not answer.get("recordingPath"):
        raise HTTPException(status_code=404, detail="该题没有可回放的录音")
    root = _recordings_root()
    path = (root / answer["recordingPath"]).resolve()
    if not str(path).startswith(str(root)) or not path.exists():
        raise HTTPException(status_code=404, detail="录音文件不存在")
    return FileResponse(path, media_type="audio/wav")


@router.delete("/{session_id}")
def delete_session(session_id: str, user_id: str = Depends(get_current_user)) -> dict:
    """删除面试会话（连带作答/追问/录音）；合规「可删」口径，仅本人。"""
    _require_session(user_id, session_id)
    store.drop_interview_session(session_id)
    _delete_recordings(session_id)
    return {"ok": True}
