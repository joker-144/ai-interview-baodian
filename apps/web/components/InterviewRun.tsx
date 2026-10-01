"use client";

/**
 * 面试进行时（五期，文档 3.9）：/interview 页的作答主体。
 *
 * 单题闭环：读题（TTS）→ 录音/文字作答 → 提交（后端 ASR + light 评分）→
 * 右栏即时回显 STAR 四维 + 命中关键词 → 一层追问 → 下一题。
 * 语音链路任一环节不可用（无 ASR / 无麦克风权限）自动降级文字输入；
 * 顶部计时 + 进度，随时可「结束面试」交由 primary 层生成复盘报告。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Waveform } from "@/components/Waveform";
import { ProgressBar } from "@/components/ui";
import { WavPlayer, WavRecorder, probeMicrophone } from "@/lib/audio";
import {
  finishInterview,
  submitInterviewAnswer,
  submitInterviewFollowup,
  synthesizeInterviewTts,
} from "@/lib/api";
import {
  INTERVIEW_MODE_LABEL,
  STAR_LABEL,
  type InterviewAnswerResult,
  type InterviewCapabilities,
  type InterviewSession,
  type StarScores,
} from "@/lib/types";

const DIM_TAG: Record<string, string> = {
  STAR: "bg-purple-50 text-ai",
  技术: "bg-brand-light text-brand",
  HR: "bg-orange-50 text-warn",
};

function fmt(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  return `${Math.floor(s / 60).toString().padStart(2, "0")}:${(s % 60).toString().padStart(2, "0")}`;
}

function elapsedFrom(startedAt: string): number {
  const t = new Date(startedAt.replace(" ", "T")).getTime();
  return Number.isFinite(t) ? Math.max(0, Math.round((Date.now() - t) / 1000)) : 0;
}

/** STAR 四维条形（0~10） */
function StarBars({ scores }: { scores: StarScores | null }) {
  const keys: (keyof StarScores)[] = ["situation", "task", "action", "result"];
  if (!scores) return <p className="py-6 text-center text-sm text-muted">提交作答后显示评分</p>;
  return (
    <ul className="space-y-2.5">
      {keys.map((k) => {
        const v = Math.max(0, Math.min(10, scores[k] ?? 0));
        return (
          <li key={k}>
            <div className="flex items-center justify-between text-xs">
              <span className="text-ink">{STAR_LABEL[k]}</span>
              <span className="font-medium text-muted">{v} / 10</span>
            </div>
            <div className="mt-1 h-1.5 w-full rounded-full bg-line">
              <div
                className={`h-full rounded-full ${v >= 8 ? "bg-success" : v >= 5 ? "bg-brand" : "bg-warn"}`}
                style={{ width: `${Math.max(v * 10, 4)}%` }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function InterviewRun({
  session,
  capabilities,
  onFinished,
  onAbandoned,
}: {
  session: InterviewSession;
  capabilities: InterviewCapabilities;
  onFinished: (sessionId: string) => void;
  onAbandoned: () => void;
}) {
  const questions = session.questions;
  const initialAnswers = useMemo(
    () => Object.fromEntries((session.answers ?? []).map((a) => [a.seq, a])),
    [session.answers],
  );

  const [answers, setAnswers] = useState<Record<number, InterviewAnswerResult>>(initialAnswers);
  const [followups, setFollowups] = useState<Record<number, string>>({});
  const [seq, setSeq] = useState(() => {
    const firstTodo = questions.findIndex((_, i) => !initialAnswers[i]);
    return firstTodo === -1 ? Math.max(0, questions.length - 1) : firstTodo;
  });
  const [reanswer, setReanswer] = useState(false);
  const [elapsed, setElapsed] = useState(() => elapsedFrom(session.startedAt));

  // 作答草稿（语音/文字共用一份，target 决定提交去向）
  const [draftMode, setDraftMode] = useState<"voice" | "text">("voice");
  const [draftBlob, setDraftBlob] = useState<Blob | null>(null);
  const [draftDur, setDraftDur] = useState(0);
  const [draftText, setDraftText] = useState("");
  const [isRecording, setIsRecording] = useState(false);
  const [recordSec, setRecordSec] = useState(0);
  const [recAnalyser, setRecAnalyser] = useState<AnalyserNode | null>(null);

  // TTS
  const [ttsLoading, setTtsLoading] = useState(false);
  const [ttsPlaying, setTtsPlaying] = useState(false);
  const [ttsAnalyser, setTtsAnalyser] = useState<AnalyserNode | null>(null);

  const [micOk, setMicOk] = useState<boolean | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmFinish, setConfirmFinish] = useState(false);

  const recorderRef = useRef<WavRecorder | null>(null);
  const playerRef = useRef<WavPlayer | null>(null);
  const recTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const voiceCapable = capabilities.asrAvailable && micOk !== false;
  const question = questions[seq];
  const currentAnswer = answers[seq];
  const reanswering = reanswer || !currentAnswer;
  const pendingFollowUp = Boolean(currentAnswer?.followUp) && followups[seq] === undefined;
  const target: "answer" | "followup" | null = reanswering ? "answer" : pendingFollowUp ? "followup" : null;
  const answeredCount = Object.keys(answers).length;

  // 计时（面试进行态每秒走）
  useEffect(() => {
    const t = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(t);
  }, []);

  // 麦克风可用性探测（仅当后端 ASR 就绪才探测，避免无谓权限弹窗）
  useEffect(() => {
    let alive = true;
    if (capabilities.asrAvailable) {
      probeMicrophone().then((ok) => {
        if (alive) {
          setMicOk(ok);
          setDraftMode(ok ? "voice" : "text");
        }
      });
    } else {
      setDraftMode("text");
    }
    return () => {
      alive = false;
    };
  }, [capabilities.asrAvailable]);

  // 切题重置草稿
  const resetDraft = useCallback(() => {
    setDraftBlob(null);
    setDraftDur(0);
    setDraftText("");
    setIsRecording(false);
    setRecordSec(0);
    setRecAnalyser(null);
    setReanswer(false);
  }, []);

  useEffect(() => {
    resetDraft();
    setDraftMode(voiceCapable ? "voice" : "text");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seq]);

  // 卸载清理：释放录音器与播放器
  useEffect(() => {
    return () => {
      if (recTimerRef.current) clearInterval(recTimerRef.current);
      recorderRef.current?.cancel();
      playerRef.current?.close();
    };
  }, []);

  const stopRecTimer = () => {
    if (recTimerRef.current) {
      clearInterval(recTimerRef.current);
      recTimerRef.current = null;
    }
  };

  const onStartRecord = async () => {
    setError("");
    const rec = new WavRecorder();
    recorderRef.current = rec;
    try {
      await rec.start();
      setRecAnalyser(rec.getAnalyser());
      setIsRecording(true);
      setRecordSec(0);
      stopRecTimer();
      recTimerRef.current = setInterval(() => setRecordSec((s) => s + 1), 1000);
    } catch {
      setMicOk(false);
      setDraftMode("text");
      setError("无法访问麦克风，已切换为文字作答（请检查浏览器权限）");
    }
  };

  const onStopRecord = async () => {
    stopRecTimer();
    setIsRecording(false);
    setRecAnalyser(null);
    const rec = recorderRef.current;
    recorderRef.current = null;
    if (!rec) return;
    try {
      const { blob, durationSec } = await rec.stop();
      setDraftBlob(blob);
      setDraftDur(durationSec || recordSec);
    } catch {
      setError("录音处理失败，请重试或改用文字作答");
    }
  };

  const onCancelRecord = () => {
    stopRecTimer();
    setIsRecording(false);
    setRecAnalyser(null);
    recorderRef.current?.cancel();
    recorderRef.current = null;
    setDraftBlob(null);
    setDraftDur(0);
    setRecordSec(0);
  };

  const onPlayTts = async (text?: string) => {
    if (!question) return;
    setError("");
    const player = playerRef.current ?? (playerRef.current = new WavPlayer());
    await player.prepare(); // 手势内建上下文，规避自动播放告警
    setTtsAnalyser(player.getAnalyser());
    setTtsLoading(true);
    try {
      const blob = await synthesizeInterviewTts(session.id, text ? { text } : { seq });
      playerRef.current?.stop();
      setTtsPlaying(true);
      await player.play(blob);
    } catch (e) {
      setError(e instanceof Error ? e.message : "语音合成失败");
    } finally {
      setTtsPlaying(false);
      setTtsLoading(false);
    }
  };

  const onStopTts = () => {
    playerRef.current?.stop();
    setTtsPlaying(false);
  };

  const canSubmit = target === "followup" || reanswering
    ? draftMode === "voice"
      ? Boolean(draftBlob) && !isRecording
      : draftText.trim().length > 0
    : false;

  const onSubmit = async () => {
    if (!question || target === null || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      if (target === "answer") {
        const input =
          draftMode === "voice" && draftBlob
            ? { seq, file: draftBlob, durationSec: draftDur, retainAudio: session.recordAudio }
            : { seq, transcript: draftText.trim(), durationSec: draftDur, retainAudio: false };
        const result = await submitInterviewAnswer(session.id, input);
        setAnswers((m) => ({ ...m, [seq]: result }));
      } else if (currentAnswer) {
        const input =
          draftMode === "voice" && draftBlob
            ? { file: draftBlob, durationSec: draftDur, retainAudio: session.recordAudio }
            : { transcript: draftText.trim(), durationSec: draftDur, retainAudio: false };
        const r = await submitInterviewFollowup(session.id, currentAnswer.id, input);
        setFollowups((m) => ({ ...m, [seq]: r.transcript }));
      }
      resetDraft();
      setDraftMode(voiceCapable ? "voice" : "text");
    } catch (e) {
      setError(e instanceof Error ? e.message : "提交失败，请重试");
    } finally {
      setSubmitting(false);
    }
  };

  const goSeq = (next: number) => {
    if (isRecording) onCancelRecord();
    onStopTts();
    setSeq(Math.max(0, Math.min(questions.length - 1, next)));
  };

  const onFinish = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    if (isRecording) onCancelRecord();
    onStopTts();
    try {
      await finishInterview(session.id);
      onFinished(session.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "生成复盘失败，请重试");
      setBusy(false);
      setConfirmFinish(false);
    }
  };

  if (!question) {
    return <div className="py-24 text-center text-sm text-muted">题目加载异常，请返回重试</div>;
  }

  return (
    <div className="relative mx-auto max-w-5xl">
      {/* 顶栏：模式/岗位 · 题号 · 计时 · 结束面试 */}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm">
          <span className="tag bg-purple-50 text-ai">模拟面试 · {INTERVIEW_MODE_LABEL[session.mode]}</span>
          {session.targetJob && <span className="tag bg-bg text-muted">{session.targetJob}</span>}
          {session.recordAudio && <span className="tag bg-green-50 text-success">录音回放已开启</span>}
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm text-muted">
            第 <span className="font-semibold text-ink">{seq + 1}</span> / {questions.length} 题
          </span>
          <span className="flex items-center gap-1.5 text-sm text-muted">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="13" r="8" />
              <path d="M12 9v4l2.5 2.5M9 2h6" strokeLinecap="round" />
            </svg>
            {fmt(elapsed)}
          </span>
          <button className="btn-primary !px-4 !py-1.5 text-xs" onClick={() => setConfirmFinish(true)} disabled={busy}>
            结束面试
          </button>
        </div>
      </div>
      <ProgressBar value={answeredCount} max={questions.length} color="bg-ai" />

      <div className="mt-4 grid grid-cols-3 gap-4">
        {/* 左：题目 + 作答 */}
        <div className="col-span-2 space-y-4">
          <section className="card p-6">
            <div className="mb-3 flex items-center gap-2">
              <span className={`tag ${DIM_TAG[question.dimension] ?? "bg-bg text-muted"}`}>{question.dimension}</span>
              <span className="text-xs text-muted">建议作答 {Math.round(question.suggestSec / 60) || 1} 分钟内</span>
              {capabilities.ttsAvailable && (
                <button
                  className="ml-auto flex items-center gap-1.5 text-xs text-brand hover:underline"
                  onClick={() => (ttsPlaying ? onStopTts() : onPlayTts())}
                  disabled={ttsLoading}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M11 5L6 9H2v6h4l5 4V5z" strokeLinejoin="round" />
                    <path d="M15.5 8.5a5 5 0 010 7" strokeLinecap="round" />
                  </svg>
                  {ttsLoading ? "合成中…" : ttsPlaying ? "停止读题" : "面试官读题"}
                </button>
              )}
            </div>
            <h2 className="text-base font-medium leading-7">{question.stem}</h2>
            {(ttsPlaying || ttsLoading) && (
              <div className="mt-3 rounded-btn bg-bg px-3 py-2">
                <Waveform analyser={ttsAnalyser} active={ttsPlaying} color="#6C5CE7" height={40} />
              </div>
            )}
          </section>

          {/* 作答区：target 非空时可作答；否则只读回显 */}
          {target !== null ? (
            <section className="card p-6">
              <div className="mb-4 flex items-center justify-between">
                <h3 className="text-sm font-semibold">
                  {target === "followup" ? "回答面试官追问" : "你的作答"}
                </h3>
                {voiceCapable && (
                  <div className="flex items-center gap-1 rounded-btn bg-bg p-0.5 text-xs">
                    {(["voice", "text"] as const).map((m) => (
                      <button
                        key={m}
                        className={`rounded-lg px-3 py-1 transition-colors ${
                          draftMode === m ? "bg-card font-medium text-brand shadow-sm" : "text-muted"
                        }`}
                        onClick={() => !isRecording && setDraftMode(m)}
                      >
                        {m === "voice" ? "语音作答" : "文字作答"}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {target === "followup" && currentAnswer?.followUp && (
                <div className="mb-4 rounded-btn border border-line bg-bg px-4 py-3 text-sm">
                  <span className="mr-2 tag bg-purple-50 text-ai">追问</span>
                  {currentAnswer.followUp}
                  {capabilities.ttsAvailable && (
                    <button className="ml-2 text-xs text-brand hover:underline" onClick={() => onPlayTts(currentAnswer.followUp ?? undefined)}>
                      读追问
                    </button>
                  )}
                </div>
              )}

              {draftMode === "voice" && voiceCapable ? (
                <div>
                  <div className="rounded-btn bg-bg px-3 py-3">
                    <Waveform analyser={recAnalyser} active={isRecording} color="#014DB2" height={56} />
                  </div>
                  <div className="mt-4 flex items-center gap-3">
                    {!isRecording && !draftBlob && (
                      <button className="btn-primary" onClick={onStartRecord}>
                        <span className="h-2.5 w-2.5 rounded-full bg-danger" /> 开始录音
                      </button>
                    )}
                    {isRecording && (
                      <>
                        <button className="btn-primary !bg-danger hover:!bg-danger" onClick={onStopRecord}>
                          <span className="h-2.5 w-2.5 rounded-sm bg-white" /> 停止录音
                        </button>
                        <span className="flex items-center gap-1.5 text-sm text-danger">
                          <span className="h-2 w-2 animate-pulse rounded-full bg-danger" />
                          录音中 {fmt(recordSec)}
                        </span>
                      </>
                    )}
                    {!isRecording && draftBlob && (
                      <>
                        <button className="btn-secondary" onClick={onCancelRecord}>重录</button>
                        <span className="text-sm text-muted">已录制 {fmt(draftDur)}</span>
                      </>
                    )}
                    <div className="flex-1" />
                    <button className="btn-primary" onClick={onSubmit} disabled={!canSubmit || submitting}>
                      {submitting ? "评分中…" : "提交"}
                    </button>
                  </div>
                  <p className="mt-3 text-xs text-muted">
                    录音经本地 SenseVoice 离线转写后评分；
                    {session.recordAudio ? "本场已开启留存，可在复盘回放。" : "默认不留存，转写后即弃。"}
                  </p>
                </div>
              ) : (
                <div>
                  <textarea
                    className="input min-h-28 resize-y"
                    placeholder={
                      capabilities.asrAvailable
                        ? "输入你的回答（也可直接口述由本地语音识别转写）…"
                        : "本地语音识别未就绪，请输入你的回答…"
                    }
                    value={draftText}
                    onChange={(e) => setDraftText(e.target.value)}
                  />
                  <div className="mt-3 flex items-center gap-3">
                    <span className="text-xs text-muted">{draftText.trim().length} 字</span>
                    <div className="flex-1" />
                    <button className="btn-primary" onClick={onSubmit} disabled={!canSubmit || submitting}>
                      {submitting ? "评分中…" : "提交"}
                    </button>
                  </div>
                </div>
              )}
            </section>
          ) : (
            <section className="card p-6">
              <h3 className="mb-2 text-sm font-semibold">本题作答</h3>
              <p className="whitespace-pre-wrap rounded-btn bg-bg px-4 py-3 text-sm leading-6">
                {currentAnswer?.transcript || "（无转写内容）"}
              </p>
              {currentAnswer?.comment && (
                <p className="mt-3 text-sm text-muted">点评：{currentAnswer.comment}</p>
              )}
              {followups[seq] !== undefined && (
                <div className="mt-4 rounded-btn border border-line px-4 py-3">
                  <p className="text-xs text-muted">追问 · 面试官</p>
                  <p className="mt-1 text-sm">{currentAnswer?.followUp}</p>
                  <p className="mt-2 text-xs text-muted">追问 · 你的回答</p>
                  <p className="mt-1 whitespace-pre-wrap text-sm">{followups[seq] || "（未作答）"}</p>
                </div>
              )}
              <div className="mt-4 flex items-center gap-3">
                <button className="btn-secondary" onClick={() => setReanswer(true)}>重新作答</button>
                <div className="flex-1" />
                {seq > 0 && <button className="btn-secondary" onClick={() => goSeq(seq - 1)}>上一题</button>}
                {seq < questions.length - 1 ? (
                  <button className="btn-primary" onClick={() => goSeq(seq + 1)}>下一题</button>
                ) : (
                  <button className="btn-primary" onClick={() => setConfirmFinish(true)}>结束并生成复盘</button>
                )}
              </div>
            </section>
          )}
        </div>

        {/* 右：即时评估（STAR + 命中关键词 + 点评） */}
        <div className="col-span-1 space-y-4">
          <section className="card p-5">
            <h3 className="mb-3 text-sm font-semibold">STAR 四维评分</h3>
            <StarBars scores={currentAnswer?.starScores ?? null} />
          </section>
          <section className="card p-5">
            <h3 className="mb-3 text-sm font-semibold">关键词命中</h3>
            {!currentAnswer ? (
              <p className="py-4 text-center text-sm text-muted">提交作答后检测命中情况</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {currentAnswer.hitKeywords.map((k) => (
                  <span key={`h-${k}`} className="tag bg-green-50 text-success">✓ {k}</span>
                ))}
                {currentAnswer.missedKeywords.map((k) => (
                  <span key={`m-${k}`} className="tag bg-bg text-muted">{k}</span>
                ))}
                {currentAnswer.hitKeywords.length === 0 && currentAnswer.missedKeywords.length === 0 && (
                  <span className="text-xs text-muted">本题未设参考关键词</span>
                )}
              </div>
            )}
            <p className="mt-3 text-xs text-muted">绿=已命中 · 灰=未提及</p>
          </section>
        </div>
      </div>

      {/* 结束确认：明示未答题数 */}
      {confirmFinish && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 px-4">
          <div className="card w-full max-w-sm p-6">
            <h3 className="text-lg font-semibold">结束面试？</h3>
            <p className="mt-2 text-sm text-muted">
              已作答 <strong className="text-ink">{answeredCount}</strong> / {questions.length} 题，
              结束后将基于全部问答生成复盘报告（未答题目不计入）。
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button className="btn-secondary !px-4 !py-2 text-sm" onClick={() => setConfirmFinish(false)} disabled={busy}>
                继续面试
              </button>
              <button className="btn-primary !px-4 !py-2 text-sm" onClick={onFinish} disabled={busy}>
                {busy ? "生成复盘中…" : "确认结束"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 放弃（不计入复盘，直接退出） */}
      <button className="mt-4 text-xs text-muted hover:text-ink" onClick={onAbandoned} disabled={busy}>
        ← 退出面试（不生成复盘）
      </button>

      {error && (
        <div className="fixed bottom-20 left-1/2 z-50 -translate-x-1/2 rounded-full bg-danger px-5 py-2.5 text-sm text-white shadow-lg">
          {error}
        </div>
      )}
    </div>
  );
}
