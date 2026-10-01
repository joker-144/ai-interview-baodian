"use client";

/**
 * AI 模拟面试（五期，文档 3.9）：/interview
 *
 * 三态：开场配置（模式/岗位/题数/录音开关 + 能力探测 + 历史）→ 出题加载 → 面试进行时。
 * 出题按「目标岗位 + 简历」实时动态生成（不落题库）；结束后跳复盘报告页。
 * 语音能力（ASR/TTS）由后端探测，任一不可用时页面自动降级并给出提示。
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { InterviewRun } from "@/components/InterviewRun";
import { PageHeader } from "@/components/ui";
import {
  createInterviewSession,
  deleteInterviewSession,
  getInterviewCapabilities,
  getInterviewSession,
  getInterviewSessions,
  getMe,
  isLoggedIn,
} from "@/lib/api";
import {
  INTERVIEW_MODE_HINT,
  INTERVIEW_MODE_LABEL,
  type InterviewCapabilities,
  type InterviewListItem,
  type InterviewMode,
  type InterviewSession,
} from "@/lib/types";

const MODES: InterviewMode[] = ["mixed", "tech", "behavior", "hr"];
const COUNTS = [5, 8, 10];

export default function InterviewPage() {
  const router = useRouter();
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [phase, setPhase] = useState<"setup" | "loading" | "run">("setup");
  const [caps, setCaps] = useState<InterviewCapabilities | null>(null);
  const [history, setHistory] = useState<InterviewListItem[]>([]);

  const [mode, setMode] = useState<InterviewMode>("mixed");
  const [targetJob, setTargetJob] = useState("");
  const [count, setCount] = useState(8);
  const [recordAudio, setRecordAudio] = useState(false);

  const [session, setSession] = useState<InterviewSession | null>(null);
  const [error, setError] = useState("");

  const loadHistory = useCallback(() => {
    getInterviewSessions()
      .then(setHistory)
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!isLoggedIn()) {
      setAuthed(false);
      return;
    }
    setAuthed(true);
    getInterviewCapabilities().then(setCaps).catch(() => {});
    getMe()
      .then((m) => setTargetJob((prev) => prev || m.profile.targetRole || ""))
      .catch(() => {});
    loadHistory();
  }, [loadHistory]);

  const onStart = async () => {
    setError("");
    setPhase("loading");
    try {
      const s = await createInterviewSession({ mode, targetJob: targetJob.trim(), count, recordAudio });
      setSession(s);
      setPhase("run");
    } catch (e) {
      setError(e instanceof Error ? e.message : "面试题生成失败，请重试");
      setPhase("setup");
    }
  };

  const onResume = async (id: string) => {
    setError("");
    setPhase("loading");
    try {
      const s = await getInterviewSession(id);
      if (s.status === "finished") {
        router.push(`/interview/report/${id}`);
        setPhase("setup");
        return;
      }
      setSession(s);
      setPhase("run");
    } catch (e) {
      setError(e instanceof Error ? e.message : "恢复面试失败");
      setPhase("setup");
    }
  };

  const onDelete = async (id: string) => {
    try {
      await deleteInterviewSession(id);
      loadHistory();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    }
  };

  const onFinished = (id: string) => {
    router.push(`/interview/report/${id}`);
  };

  const onAbandoned = () => {
    setSession(null);
    setPhase("setup");
    loadHistory();
  };

  if (authed === null) {
    return <div className="py-24 text-center text-sm text-muted">加载中…</div>;
  }
  if (!authed) {
    return (
      <div className="card mx-auto mt-16 max-w-md p-8 text-center">
        <h1 className="text-lg font-semibold">登录后开始模拟面试</h1>
        <p className="mt-2 text-sm text-muted">语音问答 + AI 面试官追问，STAR 四维实时评估与复盘报告。</p>
        <Link href="/login" className="btn-primary mt-5 inline-flex">去登录</Link>
      </div>
    );
  }

  if (phase === "run" && session) {
    return (
      <InterviewRun
        session={session}
        capabilities={caps ?? { asrAvailable: false, ttsAvailable: false }}
        onFinished={onFinished}
        onAbandoned={onAbandoned}
      />
    );
  }

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="AI 模拟面试" sub="按你的简历与目标岗位动态出题，语音作答，AI 面试官即时评分与追问" />

      {error && <div className="card mb-4 border-warn/30 bg-warn/5 px-4 py-3 text-sm text-warn">{error}</div>}

      {phase === "loading" ? (
        <div className="card flex flex-col items-center py-20">
          <span className="h-8 w-8 animate-spin rounded-full border-2 border-line border-t-brand" />
          <p className="mt-4 text-sm text-muted">AI 面试官正在结合你的简历出题，约需数秒…</p>
        </div>
      ) : (
        <>
          {/* 能力探测提示 */}
          {caps && (!caps.asrAvailable || !caps.ttsAvailable) && (
            <div className="card mb-4 border-line bg-bg px-4 py-3 text-xs text-muted">
              {!caps.asrAvailable && <p>· 本地语音识别未就绪：作答将降级为文字输入（可在管理端下载 SenseVoice 权重）。</p>}
              {!caps.ttsAvailable && <p>· 云端语音合成未配置：暂不提供「面试官读题」（可在管理端 voice 层填写 Qwen3-TTS 的 API Key）。</p>}
            </div>
          )}

          <section className="card p-6">
            {/* 模式 */}
            <h3 className="mb-2 text-sm font-semibold">面试类型</h3>
            <div className="flex flex-wrap gap-2">
              {MODES.map((m) => (
                <button
                  key={m}
                  className={`chip ${mode === m ? "chip-active" : ""}`}
                  onClick={() => setMode(m)}
                >
                  {INTERVIEW_MODE_LABEL[m]}
                </button>
              ))}
            </div>
            <p className="mt-2 text-xs text-muted">{INTERVIEW_MODE_HINT[mode]}</p>

            {/* 目标岗位 */}
            <h3 className="mb-2 mt-6 text-sm font-semibold">目标岗位</h3>
            <input
              className="input"
              placeholder="如：后端开发工程师（留空则用简历中的目标岗位）"
              value={targetJob}
              onChange={(e) => setTargetJob(e.target.value)}
              maxLength={40}
            />

            {/* 题数 */}
            <h3 className="mb-2 mt-6 text-sm font-semibold">题目数量</h3>
            <div className="flex gap-2">
              {COUNTS.map((c) => (
                <button key={c} className={`chip ${count === c ? "chip-active" : ""}`} onClick={() => setCount(c)}>
                  {c} 题
                </button>
              ))}
            </div>

            {/* 录音合规开关 */}
            <label className="mt-6 flex cursor-pointer items-start gap-3 rounded-btn border border-line bg-bg px-4 py-3">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 accent-[#014DB2]"
                checked={recordAudio}
                onChange={(e) => setRecordAudio(e.target.checked)}
              />
              <span className="text-sm">
                <span className="font-medium text-ink">本场录制我的回答以便回放</span>
                <span className="mt-0.5 block text-xs text-muted">
                  默认不录制：麦克风始终用于语音识别转写，但音频转写后即弃。开启后本场录音才落盘，可在复盘页回放，随时可删。
                </span>
              </span>
            </label>

            <button className="btn-primary mt-6 w-full" onClick={onStart}>
              开始面试
            </button>
          </section>

          {/* 历史面试 */}
          <section className="card mt-4 p-5">
            <h3 className="mb-3 text-sm font-semibold">历史面试</h3>
            {history.length === 0 ? (
              <p className="py-4 text-center text-sm text-muted">还没有面试记录，开始你的第一场模拟面试吧</p>
            ) : (
              <ul className="divide-y divide-line">
                {history.map((h) => (
                  <li key={h.id} className="flex items-center gap-3 py-3">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-ink">
                        {INTERVIEW_MODE_LABEL[h.mode]}
                        {h.targetJob ? ` · ${h.targetJob}` : ""}
                      </p>
                      <p className="mt-0.5 text-xs text-muted">
                        {h.startedAt} · {h.total} 题 ·{" "}
                        {h.status === "finished" ? (
                          <span className="text-success">已完成</span>
                        ) : (
                          <span className="text-warn">进行中</span>
                        )}
                      </p>
                    </div>
                    {h.hasReport && (
                      <button className="btn-secondary !px-3 !py-1.5 text-xs" onClick={() => router.push(`/interview/report/${h.id}`)}>
                        查看复盘
                      </button>
                    )}
                    {h.status !== "finished" && (
                      <button className="btn-primary !px-3 !py-1.5 text-xs" onClick={() => onResume(h.id)}>
                        继续
                      </button>
                    )}
                    <button className="text-xs text-muted hover:text-danger" onClick={() => onDelete(h.id)}>
                      删除
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}
