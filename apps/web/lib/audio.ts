"use client";

/**
 * 语音采集与播放（五期，文档 3.9）：纯浏览器 Web Audio，无第三方依赖。
 *
 * - WavRecorder：麦克风采集 PCM → 降采样到 16k 单声道 → 编码 16bit WAV Blob（免服务端 ffmpeg）；
 *   采集期暴露 AnalyserNode 供波形可视化。ScriptProcessorNode 已废弃但 Chrome/Edge 全支持，
 *   经静音 Gain 接 destination（既驱动回调又不外放回授）。
 * - WavPlayer：把 TTS 返回的 WAV Blob 解码为 AudioBuffer 播放，同样暴露 AnalyserNode；
 *   用 BufferSource 而非 <audio>，规避 createMediaElementSource 的一次性绑定限制。
 * - 两者各自持有 AudioContext，用完 close()，避免超出浏览器上下文数量上限。
 */

const TARGET_SAMPLE_RATE = 16000; // 与后端 SenseVoice 期望采样率一致（内部虽会重采样，前端先降到 16k 省带宽）

type AudioContextCtor = typeof AudioContext;

function makeContext(): AudioContext {
  const Ctor: AudioContextCtor =
    window.AudioContext || (window as unknown as { webkitAudioContext: AudioContextCtor }).webkitAudioContext;
  return new Ctor();
}

/** 线性平均降采样：inRate → outRate（outRate ≥ inRate 时原样返回） */
function downsample(buffer: Float32Array, inRate: number, outRate: number): Float32Array {
  if (outRate >= inRate) return buffer;
  const ratio = inRate / outRate;
  const newLength = Math.round(buffer.length / ratio);
  const result = new Float32Array(newLength);
  let offsetResult = 0;
  let offsetBuffer = 0;
  while (offsetResult < result.length) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
    let accum = 0;
    let count = 0;
    for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
      accum += buffer[i];
      count++;
    }
    result[offsetResult] = count ? accum / count : 0;
    offsetResult++;
    offsetBuffer = nextOffsetBuffer;
  }
  return result;
}

/** 编码 16bit PCM 单声道 WAV（44 字节头 + 数据） */
function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeStr = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i));
  };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // 单声道
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate = rate * channels * bytesPerSample
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeStr(36, "data");
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([view], { type: "audio/wav" });
}

export interface RecordingResult {
  blob: Blob;
  durationSec: number;
}

/** 麦克风录音器：start() 申请权限并采集，stop() 返回 16k 单声道 WAV */
export class WavRecorder {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private processor: ScriptProcessorNode | null = null;
  private analyser: AnalyserNode | null = null;
  private chunks: Float32Array[] = [];
  private inputRate = 48000;
  private startedAt = 0;

  async start(): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    this.ctx = makeContext();
    await this.ctx.resume(); // 用户手势内触发，满足自动播放策略
    this.inputRate = this.ctx.sampleRate;
    this.source = this.ctx.createMediaStreamSource(this.stream);
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 1024;
    this.source.connect(this.analyser);

    this.processor = this.ctx.createScriptProcessor(4096, 1, 1);
    this.chunks = [];
    this.processor.onaudioprocess = (e) => {
      const data = e.inputBuffer.getChannelData(0);
      this.chunks.push(new Float32Array(data));
    };
    // 经静音 Gain 接 destination：驱动 onaudioprocess 回调又不把麦克风外放（回授）
    const mute = this.ctx.createGain();
    mute.gain.value = 0;
    this.source.connect(this.processor);
    this.processor.connect(mute);
    mute.connect(this.ctx.destination);
    this.startedAt = Date.now();
  }

  getAnalyser(): AnalyserNode | null {
    return this.analyser;
  }

  isRecording(): boolean {
    return this.processor !== null;
  }

  async stop(): Promise<RecordingResult> {
    const durationSec = this.startedAt ? Math.round((Date.now() - this.startedAt) / 1000) : 0;
    // 合并所有 PCM 分片
    const total = this.chunks.reduce((n, c) => n + c.length, 0);
    const merged = new Float32Array(total);
    let off = 0;
    for (const c of this.chunks) {
      merged.set(c, off);
      off += c.length;
    }
    this.teardown();
    const down = downsample(merged, this.inputRate, TARGET_SAMPLE_RATE);
    return { blob: encodeWav(down, TARGET_SAMPLE_RATE), durationSec };
  }

  /** 放弃本次录音（不产出 Blob），仅释放资源 */
  cancel(): void {
    this.chunks = [];
    this.teardown();
  }

  private teardown(): void {
    try {
      this.processor?.disconnect();
      this.source?.disconnect();
      this.analyser?.disconnect();
      this.stream?.getTracks().forEach((t) => t.stop());
      this.ctx?.close();
    } catch {
      // 释放失败不阻断主流程
    }
    this.processor = null;
    this.source = null;
    this.analyser = null;
    this.stream = null;
    this.ctx = null;
    this.chunks = [];
  }
}

/** TTS 音频播放器：解码 WAV Blob 播放，暴露 AnalyserNode 供聆听态波形 */
export class WavPlayer {
  private ctx: AudioContext | null = null;
  private source: AudioBufferSourceNode | null = null;
  private analyser: AnalyserNode | null = null;

  private ensure(): AudioContext {
    if (!this.ctx) {
      this.ctx = makeContext();
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 1024;
      this.analyser.connect(this.ctx.destination);
    }
    return this.ctx;
  }

  getAnalyser(): AnalyserNode | null {
    return this.analyser;
  }

  /** 在用户手势内预建上下文并 resume（早于网络请求 await，规避自动播放限制告警） */
  async prepare(): Promise<void> {
    const ctx = this.ensure();
    await ctx.resume();
  }

  /** 播放一段 WAV Blob，resolve 于播放自然结束或被 stop 打断 */
  async play(blob: Blob): Promise<void> {
    const ctx = this.ensure();
    await ctx.resume();
    const arrayBuffer = await blob.arrayBuffer();
    const audioBuffer = await ctx.decodeAudioData(arrayBuffer);
    this.stop();
    return new Promise<void>((resolve) => {
      const src = ctx.createBufferSource();
      src.buffer = audioBuffer;
      src.connect(this.analyser as AnalyserNode);
      src.onended = () => {
        if (this.source === src) this.source = null;
        resolve();
      };
      this.source = src;
      src.start();
    });
  }

  stop(): void {
    if (this.source) {
      try {
        this.source.onended = null;
        this.source.stop();
        this.source.disconnect();
      } catch {
        // 已停止则忽略
      }
      this.source = null;
    }
  }

  close(): void {
    this.stop();
    try {
      this.analyser?.disconnect();
      this.ctx?.close();
    } catch {
      // 忽略
    }
    this.ctx = null;
    this.analyser = null;
  }
}

/** 探测麦克风权限/可用性：不可用时前端降级文字输入 */
export async function probeMicrophone(): Promise<boolean> {
  if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) return false;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop());
    return true;
  } catch {
    return false;
  }
}
