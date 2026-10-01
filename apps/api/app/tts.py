"""云端 TTS 运行时：Qwen3-TTS-Flash（阿里云百炼 DashScope），题目文字 → 面试官语音。

为什么云端 TTS（五期语音模拟面试）：
- 中英混排、数字、百分号（如「STAR」「35%」）朗读远好于本地 Kokoro（实测 Kokoro 把 STAR 读成
  「t而」、35% 读成「353」），面试官读题的自然度直接影响面试体验；
- DashScope 送 11 万字符（≈110 场面试）、¥0.8/万字符、输出音频免费，边际成本可忽略；
- **非流式**：一次性返回音频 URL（24h 有效），后端下载音频字节同源回传前端播放，
  避免 MSE 分片播放复杂度（题目生成本就有数秒延迟，非流式 +1~2s 可接受）。

约定（对齐 llm.py / asr.py）：
- 配置取 store.state.llm_config["voice"]（provider=qwen / modelName=qwen3-tts-flash / apiKey 混淆存储），
  复用 llm.layer_config 做启用与 Key 校验；
- **裸 HTTP（requests）**，不引 DashScope SDK；端点走 DashScope 原生 api/v1（非文本层的 compatible-mode/v1）；
- 单次文本有 512 Token 上限，超长题目按句切分逐段合成后拼接 WAV；
- 限流 / 超时 / 鉴权失败抛 TtsError（可读），由路由层转 HTTP 错误，绝不让服务崩溃。
"""

from __future__ import annotations

import asyncio
import base64
import io
import time
import wave
from typing import Any

import requests

from app.keys import deobfuscate
from app.llm import LlmError, layer_config

DEFAULT_MODEL = "qwen3-tts-flash"
DEFAULT_VOICE = "Andre"  # 安德雷·沉稳磁性男声（管理端可切）
DEFAULT_LANG = "Chinese"
DEFAULT_BASE = "https://dashscope.aliyuncs.com/api/v1"
TTS_PATH = "/services/aigc/multimodal-generation/generation"

# 单次合成文本上限：qwen3-tts-flash 输入 512 Token，中文约 1 字/Token，取 480 字留安全余量；
# 英文 1 Token≈4 字符，480 字符远低于上限，故对中英混排均安全。超长题目按句切分。
MAX_CHARS_PER_SEGMENT = 480
_SENT_END = "。！？；!?;\n"

# 单次调用重试次数（网络抖动 / 5xx / 429）；4xx 参数或鉴权问题直接抛出不重试
MAX_ATTEMPTS = 2
RETRY_BACKOFF_SEC = 1.5


class TtsError(RuntimeError):
    """语音合成失败（配置缺失 / 鉴权失败 / 限流 / 超时 / 返回不可解析）。"""


def _endpoint(cfg: dict[str, Any]) -> str:
    """构造 DashScope 原生 TTS 端点；兼容模式地址（文本层用）自动回退到 api/v1。"""
    base = (cfg.get("baseUrl") or "").strip().rstrip("/")
    if "/compatible-mode" in base:  # qwen 文本层默认地址不能调 TTS
        base = base.split("/compatible-mode")[0] + "/api/v1"
    if not base:
        base = DEFAULT_BASE
    return base + TTS_PATH


def _split_text(text: str, budget: int = MAX_CHARS_PER_SEGMENT) -> list[str]:
    """把长文本按句末标点切成若干段（每段 ≤ budget 字），尽量在自然停顿处断开。"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= budget:
        return [text]

    sentences: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in _SENT_END:
            sentences.append(buf)
            buf = ""
    if buf:
        sentences.append(buf)

    segments: list[str] = []
    cur = ""
    for sent in sentences:
        while len(sent) > budget:  # 单句就超长：按预算硬切
            if cur:
                segments.append(cur)
                cur = ""
            segments.append(sent[:budget])
            sent = sent[budget:]
        if len(cur) + len(sent) > budget:
            segments.append(cur)
            cur = sent
        else:
            cur += sent
    if cur:
        segments.append(cur)
    return [s for s in segments if s.strip()]


def _concat_wav(parts: list[bytes]) -> bytes:
    """拼接多段 WAV（同一音色/模型，参数一致）；单段时原样返回。"""
    if len(parts) == 1:
        return parts[0]
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        header_set = False
        for blob in parts:
            with wave.open(io.BytesIO(blob), "rb") as reader:
                if not header_set:
                    writer.setnchannels(reader.getnchannels())
                    writer.setsampwidth(reader.getsampwidth())
                    writer.setframerate(reader.getframerate())
                    header_set = True
                writer.writeframes(reader.readframes(reader.getnframes()))
    return out.getvalue()


def _download_audio(url: str, timeout: int) -> bytes:
    """下载音频 URL 的字节（OSS 直链，无需鉴权，24h 有效）。"""
    try:
        resp = requests.get(url, timeout=timeout)
    except requests.RequestException as exc:
        raise TtsError(f"下载合成音频失败：{exc}") from exc
    if resp.status_code >= 400:
        raise TtsError(f"下载合成音频失败：HTTP {resp.status_code}")
    if not resp.content:
        raise TtsError("合成音频为空")
    return resp.content


def _request(
    cfg: dict[str, Any], text: str, voice: str, language: str, model: str, timeout: int
) -> tuple[bytes, dict[str, Any]]:
    """合成单段文本，返回 (音频字节, usage)。含 5xx/429 重试；4xx 直接抛可读错误。"""
    url = _endpoint(cfg)
    key = deobfuscate(cfg.get("apiKey", ""))
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {"model": model, "input": {"text": text, "voice": voice, "language_type": language}}

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last_error = TtsError(f"请求 DashScope TTS 失败：{exc}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SEC)
                continue
            raise last_error from exc

        if resp.status_code >= 400:
            try:
                err = resp.json()
                detail = f'{err.get("code", "")} {err.get("message", "")}'.strip() or resp.text[:200]
            except ValueError:
                detail = resp.text[:200]
            # 401/403/400 是配置或参数问题，重试无益；429 限流与 5xx 可重试一次
            if resp.status_code < 500 and resp.status_code != 429:
                raise TtsError(f"DashScope TTS 返回 {resp.status_code}：{detail}")
            last_error = TtsError(f"DashScope TTS 返回 {resp.status_code}：{detail}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SEC)
                continue
            raise last_error

        try:
            payload = resp.json()
        except ValueError as exc:
            raise TtsError("DashScope TTS 返回非 JSON 响应") from exc

        audio = (payload.get("output") or {}).get("audio") or {}
        audio_url = audio.get("url")
        usage = payload.get("usage") or {}
        if audio_url:
            return _download_audio(audio_url, timeout), usage
        if audio.get("data"):  # 兜底：部分模式内联返回 base64 音频
            return base64.b64decode(audio["data"]), usage
        raise TtsError("响应未含音频 URL（output.audio.url），请核对模型名与音色是否受支持")

    raise last_error if last_error else TtsError("调用失败")


def synthesize(
    text: str,
    *,
    voice: str = "",
    language: str = "",
    model: str = "",
    timeout: int | None = None,
) -> dict[str, Any]:
    """文字 → 音频字节。返回 {audio, contentType, chars, segments, latencyMs, voice}。

    voice/language/model 缺省时取 voice 分层配置（params.ttsVoice / params.languageType / modelName）。
    """
    cfg = layer_config("voice")  # 复用 llm 校验：未启用 / 缺 Key / 缺 baseUrl 时抛可读错误
    params = cfg.get("params") or {}
    voice = (voice or params.get("ttsVoice") or DEFAULT_VOICE).strip()
    language = (language or params.get("languageType") or DEFAULT_LANG).strip()
    model = (model or cfg.get("modelName") or DEFAULT_MODEL).strip()
    tmo = int(timeout or params.get("timeoutSec", 30))

    segments = _split_text(text)
    if not segments:
        raise TtsError("合成文本为空")

    started = time.time()
    parts: list[bytes] = []
    chars = 0
    for seg in segments:
        blob, usage = _request(cfg, seg, voice, language, model, tmo)
        parts.append(blob)
        chars += int(usage.get("characters") or len(seg))
    audio = _concat_wav(parts)
    return {
        "audio": audio,
        "contentType": "audio/wav",
        "chars": chars,
        "segments": len(segments),
        "latencyMs": int((time.time() - started) * 1000),
        "voice": voice,
    }


async def asynthesize(text: str, **kwargs: Any) -> dict[str, Any]:
    """异步合成（线程池执行，避免阻塞事件循环）。"""
    return await asyncio.to_thread(lambda: synthesize(text, **kwargs))


# 自测样本刻意包含中英混排（STAR）+ 百分号（35%）+ 数字，验证选云端而非本地 Kokoro 的核心动机。
SAMPLE_TEXT = (
    "你好，我是今天的面试官。请用 STAR 方式讲讲你最有成就感的一个项目，"
    "背景部分控制在 35% 的篇幅，重点说清你的角色与量化结果。"
)


def selftest() -> dict[str, Any]:
    """连通性自测：真实合成一小段文字，回传真实延迟与音频大小（配好 Key 后第一步验证音质/中英混排）。"""
    try:
        result = synthesize(SAMPLE_TEXT)
    except (TtsError, LlmError) as exc:
        return {"ok": False, "error": str(exc), "detail": ""}
    size_kb = len(result["audio"]) // 1024
    ok = size_kb >= 1
    detail = (
        f'云端 TTS 合成 {result["chars"]} 字符（音色 {result["voice"]}，{result["segments"]} 段）'
        f'耗时 {result["latencyMs"]}ms，音频 {size_kb}KB'
    )
    return {
        "ok": ok,
        "latencyMs": result["latencyMs"],
        "detail": detail,
        "error": None if ok else "音频过短，请核对音色与模型名是否受支持",
    }
