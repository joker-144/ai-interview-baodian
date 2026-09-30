"use client";

/**
 * 模拟考试作答页（P11，二期批 2）：/exam/[examId]
 *
 * - 断网/刷新恢复：GET /api/exams/{id} 原样返回 running 现场，已答答案回填；
 * - 倒计时以「后端 elapsedSec + 本地时钟」推算截止时刻，到点自动交卷；
 * - 暂停口径（产品文档 822）：手动暂停，或页面隐藏超 10 分钟自动计暂停
 *   （暂停段不扣时长，报告显示「含暂停」）；hidden 未满 10 分钟时间照走；
 * - 考试态不回显对错，交卷前二次确认未完成题数，支持「只交已答」。
 */

import { useRouter } from "next/navigation";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ProgressBar } from "@/components/ui";
import {
  abandonExam,
  answerExam,
  getExamState,
  pauseExam,
  resumeExam,
  submitExam,
} from "@/lib/api";
import type { ExamState } from "@/lib/types";

const OPTION_LETTERS = ["A", "B", "C", "D", "E", "F"];
const AUTO_PAUSE_AFTER_MS = 10 * 60 * 1000; // hidden 超 10 分钟才计暂停

function fmt(sec: number) {
  const s = Math.max(sec, 0);
  return `${Math.floor(s / 60)
    .toString()
    .padStart(2, "0")}:${(s % 60).toString().padStart(2, "0")}`;
}

export default function ExamPage() {
  const router = useRouter();
  const params = useParams<{ examId: string }>();
  const examId = params.examId;

  const [exam, setExam] = useState<ExamState | null>(null);
  const [index, setIndex] = useState(0);
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [remaining, setRemaining] = useState(0);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const endAtRef = useRef<number>(0); // 截止时刻（本地时钟推算）
  const hiddenAtRef = useRef<number>(0);
  const enteredAtRef = useRef<number>(Date.now()); // 当前题停留计时
  const submittingRef = useRef(false);

  const load = useCallback(async () => {
    try {
      const st = await getExamState(examId);
      if (st.status === "done") {
        router.replace(`/exam/report/${examId}`);
        return;
      }
      if (st.status === "abandoned") {
        router.replace("/bank");
        return;
      }
      setExam(st);
      setChoices(
        Object.fromEntries(Object.entries(st.answers).map(([k, v]) => [k, v.choice])),
      );
      const rest = st.durationSec - st.elapsedSec;
      setRemaining(rest);
      endAtRef.current = Date.now() + rest * 1000;
      const firstUnanswered = st.questions.findIndex((q) => !st.answers[q.id]);
      setIndex(firstUnanswered === -1 ? 0 : firstUnanswered);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载考试失败");
    }
  }, [examId, router]);

  useEffect(() => {
    load();
  }, [load]);

  // 本地倒计时：以截止时刻推算，hidden 未满 10 分钟时间自然照走
  useEffect(() => {
    const t = setInterval(() => {
      if (!exam || exam.status === "paused") return;
      const rest = Math.round((endAtRef.current - Date.now()) / 1000);
      setRemaining(rest);
      if (rest <= 0 && !submittingRef.current) {
        submittingRef.current = true;
        setSheetOpen(false);
        setConfirmOpen(false);
        submitExam(examId)
          .then(() => router.push(`/exam/report/${examId}`))
          .catch(() => {
            submittingRef.current = false;
            setError("自动交卷失败，请手动重试");
          });
      }
    }, 1000);
    return () => clearInterval(t);
  }, [exam, examId, router]);

  const onPauseResume = useCallback(
    async (pause: boolean) => {
      setBusy(true);
      try {
        if (pause) await pauseExam(examId);
        else await resumeExam(examId);
        // 暂停/恢复后以服务端口径重新对表
        const st = await getExamState(examId);
        setExam(st);
        endAtRef.current = Date.now() + (st.durationSec - st.elapsedSec) * 1000;
        setRemaining(st.durationSec - st.elapsedSec);
      } catch (e) {
        setError(e instanceof Error ? e.message : "操作失败");
      } finally {
        setBusy(false);
      }
    },
    [examId],
  );

  // 页面隐藏超 10 分钟 → 自动计暂停（hidden 段不扣时长）；回到可见时结算并重对表
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === "hidden") {
        hiddenAtRef.current = Date.now();
        return;
      }
      if (!hiddenAtRef.current) return;
      const hiddenMs = Date.now() - hiddenAtRef.current;
      hiddenAtRef.current = 0;
      if (hiddenMs < AUTO_PAUSE_AFTER_MS) return;
      // 未满 10 分钟时间照走（后端不知 hidden，不处理）；超了则 pause→resume 剔除该段
      (async () => {
        setBusy(true);
        try {
          const cur = await getExamState(examId);
          if (cur.status === "running") {
            await pauseExam(examId);
            await resumeExam(examId);
            const st = await getExamState(examId);
            setExam(st);
            endAtRef.current = Date.now() + (st.durationSec - st.elapsedSec) * 1000;
            setRemaining(st.durationSec - st.elapsedSec);
          }
        } catch {
          // 瞬时网络失败不阻塞考试，下次切换可见性时重新对表
        } finally {
          setBusy(false);
        }
      })();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [examId]);

  const question = exam?.questions[index];
  const answeredCount = useMemo(
    () => (exam ? Object.keys(choices).filter((k) => exam.questions.some((q) => q.id === k)).length : 0),
    [exam, choices],
  );

  const choose = async (letter: string) => {
    if (!question || !exam || exam.status !== "running" || busy) return;
    const timeSec = Math.round((Date.now() - enteredAtRef.current) / 1000);
    setChoices((c) => ({ ...c, [question.id]: letter }));
    try {
      await answerExam(examId, question.id, letter, timeSec);
    } catch {
      // 不直出后端状态文本（如 Method Not Allowed），给可操作的提示；回滚本地乐观选择
      setError("作答提交失败，请检查网络后重试");
      setChoices((c) => {
        const next = { ...c };
        delete next[question.id];
        return next;
      });
    }
  };

  // 切题重置停留计时
  useEffect(() => {
    enteredAtRef.current = Date.now();
  }, [index]);

  const goSubmit = () => {
    if (!exam) return;
    setSheetOpen(false);
    setConfirmOpen(true);
  };

  const doSubmit = async () => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setBusy(true);
    try {
      await submitExam(examId);
      router.push(`/exam/report/${examId}`);
    } catch (e) {
      submittingRef.current = false;
      setError(e instanceof Error ? e.message : "交卷失败，请重试");
    } finally {
      setBusy(false);
    }
  };

  const doAbandon = async () => {
    setBusy(true);
    try {
      await abandonExam(examId);
      router.push("/bank");
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };

  if (error && !exam) {
    return <div className="card border-warn/30 bg-warn/5 mx-auto mt-10 max-w-xl px-4 py-3 text-sm text-warn">{error}</div>;
  }
  if (!exam || !question) {
    return <div className="py-24 text-center text-sm text-muted">加载中…</div>;
  }

  const paused = exam.status === "paused";
  const unanswered = exam.total - answeredCount;
  const letterOf = (qid: string) => choices[qid];

  return (
    <div className="relative mx-auto max-w-3xl">
      {/* 顶栏：退出 / 题号 / 倒计时 */}
      <div className="mb-3 flex items-center justify-between">
        <button className="btn-secondary !px-3 !py-1.5 text-xs" onClick={doAbandon} disabled={busy}>
          放弃考试
        </button>
        <span className="text-sm text-muted">
          第 <span className="font-semibold text-ink">{index + 1}</span> / {exam.questions.length} 题
        </span>
        <span className="flex items-center gap-1.5 text-sm">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="13" r="8" />
            <path d="M12 9v4l2.5 2.5M9 2h6" strokeLinecap="round" />
          </svg>
          <span className={remaining <= 60 ? "font-semibold text-danger" : "text-muted"}>{fmt(remaining)}</span>
          <button
            className="ml-2 text-xs text-brand underline-offset-2 hover:underline"
            onClick={() => onPauseResume(!paused)}
            disabled={busy}
          >
            {paused ? "继续" : "暂停"}
          </button>
        </span>
      </div>
      <ProgressBar value={answeredCount} max={exam.total} />

      {/* 题目卡（考试态不回显对错） */}
      <div className="card mt-4 p-6">
        <div className="mb-3 flex items-center gap-2">
          <span className="tag bg-purple-50 text-ai">模拟考试</span>
          <span className="tag bg-bg text-muted">{question.category}</span>
          <span className="ml-auto flex items-center gap-1 text-xs text-muted">
            难度
            {Array.from({ length: 3 }).map((_, i) => (
              <span key={i} className={`h-1.5 w-3 rounded-full ${i < question.difficulty ? "bg-warn" : "bg-line"}`} />
            ))}
          </span>
        </div>
        <h2 className="text-base font-medium leading-7">{question.stem}</h2>
        <div className="mt-5 space-y-2.5">
          {question.options.map((opt, i) => {
            const letter = OPTION_LETTERS[i];
            const chosen = letterOf(question.id) === letter;
            return (
              <button
                key={letter}
                onClick={() => choose(letter)}
                className={`flex w-full items-center gap-3 rounded-btn border px-4 py-3 text-left text-sm transition-colors ${
                  chosen ? "border-brand bg-brand-light" : "border-line hover:border-brand"
                }`}
              >
                <span
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-medium ${
                    chosen ? "bg-brand text-white" : "bg-bg text-muted"
                  }`}
                >
                  {letter}
                </span>
                <span>{opt}</span>
                {chosen && <span className="ml-auto text-xs text-muted">已选</span>}
              </button>
            );
          })}
        </div>
        {letterOf(question.id) && (
          <p className="mt-3 text-xs text-muted">已记录作答，交卷前可修改（系统以最后一次选择判分）</p>
        )}
      </div>

      {/* 底部操作 */}
      <div className="mt-4 flex items-center gap-3">
        <button className="btn-secondary" disabled={index === 0} onClick={() => setIndex((i) => i - 1)}>
          上一题
        </button>
        <button
          className="btn-secondary"
          disabled={index === exam.questions.length - 1}
          onClick={() => setIndex((i) => i + 1)}
        >
          下一题
        </button>
        <div className="flex-1" />
        <button className="btn-primary" onClick={() => setSheetOpen(true)}>
          答题卡
        </button>
      </div>

      {/* 答题卡抽屉：考试态只有已答/未答两色 */}
      {sheetOpen && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-ink/30 sm:items-center" onClick={() => setSheetOpen(false)}>
          <div className="card w-full max-w-lg rounded-b-none p-6 sm:rounded-card" onClick={(e) => e.stopPropagation()}>
            <div className="mb-1 flex items-center justify-between">
              <h3 className="text-base font-semibold">答题卡</h3>
              <button className="text-muted hover:text-ink" onClick={() => setSheetOpen(false)}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M18 6L6 18M6 6l12 12" strokeLinecap="round" />
                </svg>
              </button>
            </div>
            <p className="mb-4 text-xs text-muted">已答 {answeredCount} 题 · 未答 {unanswered} 题</p>
            <div className="grid grid-cols-8 gap-2">
              {exam.questions.map((q, i) => {
                const answered = !!choices[q.id];
                let cls = answered ? "border-brand bg-brand-light text-brand" : "border-line bg-card text-muted";
                if (i === index) cls += " ring-2 ring-brand";
                return (
                  <button
                    key={q.id}
                    className={`flex h-9 items-center justify-center rounded-btn border text-sm ${cls}`}
                    onClick={() => {
                      setIndex(i);
                      setSheetOpen(false);
                    }}
                  >
                    {i + 1}
                  </button>
                );
              })}
            </div>
            <button className="btn-primary mt-5 w-full" onClick={goSubmit} disabled={busy}>
              交卷
            </button>
          </div>
        </div>
      )}

      {/* 交卷二次确认：未完成题数明示 + 只交已答 */}
      {confirmOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 px-4">
          <div className="card w-full max-w-sm p-6">
            <h3 className="text-lg font-semibold">确认交卷？</h3>
            {unanswered > 0 ? (
              <p className="mt-2 text-sm text-muted">
                还有 <strong className="text-warn">{unanswered}</strong> 题未作答，未答题目将按错误计入。
              </p>
            ) : (
              <p className="mt-2 text-sm text-muted">已全部作答，交卷后立即生成模考报告。</p>
            )}
            <div className="mt-5 flex justify-end gap-2">
              <button className="btn-secondary !px-4 !py-2 text-sm" onClick={() => setConfirmOpen(false)} disabled={busy}>
                返回检查
              </button>
              {unanswered > 0 && (
                <button className="btn-secondary !px-4 !py-2 text-sm" onClick={doSubmit} disabled={busy}>
                  只交已答
                </button>
              )}
              <button className="btn-primary !px-4 !py-2 text-sm" onClick={doSubmit} disabled={busy}>
                {busy ? "交卷中…" : "确认交卷"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 暂停遮罩：暂停期间不计时，报告将标记「含暂停」 */}
      {paused && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-ink/50">
          <div className="card p-8 text-center">
            <p className="text-base font-semibold">考试已暂停</p>
            <p className="mt-2 text-sm text-muted">暂停期间不计时；本次考试将标记「含暂停」。</p>
            <button className="btn-primary mt-5" onClick={() => onPauseResume(false)} disabled={busy}>
              {busy ? "恢复中…" : "继续考试"}
            </button>
          </div>
        </div>
      )}

      {error && (
        <div className="fixed bottom-20 left-1/2 z-50 -translate-x-1/2 rounded-full bg-danger px-5 py-2.5 text-sm text-white shadow-lg">
          {error}
        </div>
      )}
    </div>
  );
}
