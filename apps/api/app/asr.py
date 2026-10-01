"""本地 ASR 运行时：SenseVoice-Small（sherpa-onnx），离线语音转文字，不走公网 API。

为什么本地跑 ASR（五期语音模拟面试）：
- 面试实时进行，本地推理无网络抖动、无云端限流中断（免费云 ASR 常 429）；
- 录音不出本机、转写后可即弃，合规更稳（文档第五章第 6 条）；
- SenseVoice-Small int8 实测约 22 倍实时（71s 音频 3.2s 转完）、中文错字率≈0.3%，边际成本为 0。

约定（对齐 local_models.py）：
- 权重由 scripts/download_models.py 下载到 apps/api/models/<model_id>/（model.int8.onnx + tokens.txt）；
- 懒加载 + 进程内单例：不调用不占内存，首次加载约 1s；
- 依赖缺失（未装 sherpa-onnx / 权重未下载）时抛明确错误，由管理端自测回显，绝不让服务启动失败；
- **重采样交给 sherpa 内部**：accept_waveform 接受任意采样率，内部自动重采样到 16k；
  严禁在 Python 层逐点重采样（audio.samples 是 pybind 属性，逐点访问会整体转换 C++ vector，实测卡死）。
"""

from __future__ import annotations

import array
import asyncio
import io
import time
import wave
from pathlib import Path
from typing import Any

from app.config import settings

# 进程内模型单例：{model_id: (加载耗时秒, OfflineRecognizer 实例)}
_LOADED: dict[str, tuple[float, object]] = {}


def models_dir() -> Path:
    return Path(settings.local_models_dir).resolve()


def model_folder(model_id: str = "") -> Path:
    """定位 ASR 权重目录；缺失时抛可执行指令（而非 500 堆栈）。"""
    mid = model_id or settings.asr_model_id
    folder = models_dir() / mid
    if not (folder / "model.int8.onnx").exists():
        raise RuntimeError(
            f"本地 ASR 权重缺失：{folder / 'model.int8.onnx'}"
            f"（请执行 python scripts/download_models.py --model {mid}）"
        )
    return folder


def is_available(model_id: str = "") -> bool:
    """权重是否就绪（供面试链路判断能否走语音，否则降级文字输入）。"""
    try:
        model_folder(model_id)
        return True
    except RuntimeError:
        return False


def _load(model_id: str = "") -> tuple[object, float]:
    """懒加载 SenseVoice OfflineRecognizer（进程内单例），返回 (识别器, 加载耗时秒)。"""
    folder = model_folder(model_id)
    mid = folder.name
    cached = _LOADED.get(mid)
    if cached is not None:
        return cached[1], cached[0]
    try:
        import sherpa_onnx
    except ImportError as exc:  # 依赖未装时给出可执行指令，而不是 500 堆栈
        raise RuntimeError(
            "未安装本地 ASR 依赖：pip install sherpa-onnx --index-url https://pypi.org/simple"
            "（清华等镜像无此包，必须官方 PyPI 源）"
        ) from exc

    started = time.time()
    recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(folder / "model.int8.onnx"),
        tokens=str(folder / "tokens.txt"),
        num_threads=settings.asr_threads,
        language="zh",
        use_itn=True,
    )
    elapsed = time.time() - started
    _LOADED[mid] = (elapsed, recognizer)
    return recognizer, elapsed


def _read_wav(data: bytes) -> tuple[int, list[float]]:
    """解析 16-bit PCM WAV 字节为 (采样率, 归一化 float 样本)；立体声取左声道兜底。"""
    with wave.open(io.BytesIO(data), "rb") as wf:
        sr = wf.getframerate()
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if sampwidth != 2:
        raise RuntimeError(f"仅支持 16-bit PCM WAV（收到 sampwidth={sampwidth} 字节）")
    pcm = array.array("h")
    pcm.frombytes(raw)
    if channels == 2:  # 面试录音为单声道，立体声兜底取左声道
        pcm = array.array("h", pcm[0::2])
    return sr, [s / 32768.0 for s in pcm]


def transcribe(wav_bytes: bytes, model_id: str = "") -> dict[str, Any]:
    """转写一段 WAV 音频，返回 {text, latencyMs, loadSec, sampleRate, durationSec}。"""
    recognizer, load_sec = _load(model_id)
    sr, samples = _read_wav(wav_bytes)
    duration = len(samples) / sr if sr else 0.0
    started = time.time()
    stream = recognizer.create_stream()
    stream.accept_waveform(sr, samples)  # 采样率原样传入，sherpa 内部按需重采样到 16k
    recognizer.decode_stream(stream)
    text = (stream.result.text or "").strip()
    return {
        "text": text,
        "latencyMs": int((time.time() - started) * 1000),
        "loadSec": round(load_sec, 1),
        "sampleRate": sr,
        "durationSec": round(duration, 2),
    }


async def atranscribe(wav_bytes: bytes, model_id: str = "") -> dict[str, Any]:
    """异步转写（线程池执行；sherpa 解码期释放 GIL，不阻塞事件循环）。"""
    return await asyncio.to_thread(transcribe, wav_bytes, model_id)


def selftest(model_id: str = "") -> dict[str, Any]:
    """连通性自测：用随权重下载的 test_wavs/zh.wav 真跑一次转写，回传真实延迟与文本（非 Mock）。"""
    folder = model_folder(model_id)
    sample = folder / "test_wavs" / "zh.wav"
    if not sample.exists():
        return {"ok": False, "error": f"自测样本缺失：{sample}", "detail": ""}
    try:
        result = transcribe(sample.read_bytes(), model_id)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc), "detail": ""}
    text = result["text"]
    sane = len(text) >= 2
    detail = (
        f'本地 ASR 转写 {result["durationSec"]}s 音频耗时 {result["latencyMs"]}ms'
        f'（首次加载 {result["loadSec"]}s）；识别文本「{text[:40]}」'
    )
    return {
        "ok": sane,
        "modelId": folder.name,
        "latencyMs": result["latencyMs"],
        "detail": detail,
        "error": None if sane else "识别结果为空，请核对权重与 tokens.txt",
    }
