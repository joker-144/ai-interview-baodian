"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ProgressBar, SOURCE_TAG_CLASS } from "@/components/ui";
import {
  addToWrongBook,
  getProgress,
  getQuestions,
  getQuestion,
  getSet,
  getWrongBook,
  isInWrongBook,
  resetProgress,
  submitAnswer,
  submitReview,
} from "@/lib/api";
import {
  SOURCE_LABEL,
  WRONG_REASON_LABEL,
  type Question,
  type QuestionSet,
  type WrongReason,
} from "@/lib/types";

const OPTION_LETTERS = ["A", "B", "C", "D", "E", "F"];

function fmt(sec: number) {
  const m = Math.floor(sec / 60).toString().padStart(2, "0");
  const s = (sec % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

export default function PracticePage() {
  const params = useParams<{ setId: string }>();
  const router = useRouter();
  const setId = params.setId;
  const isReview = setId === "review";

  const [setInfo, setSetInfo] = useState<QuestionSet | null>(null);
  const [questions, setQuestions] = useState<Question[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [index, setIndex] = useState(0);
  const [seconds, setSeconds] = useState(0);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [wrongAdded, setWrongAdded] = useState<Record<string, boolean>>({});
  const [reasonOpen, setReasonOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const toastTimer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    (async () => {
      if (isReview) {
        const wrong = await getWrongBook();
        const qs = await Promise.all(wrong.map((w) => getQuestion(w.questionId)));
        setQuestions(qs.filter((q): q is Question => q !== null));
        setSetInfo({ id: "review", source: "resume", title: "错题复习", questionCount: wrong.length, updatedAt: "" });
      } else {
        const [set, qs, prog] = await Promise.all([getSet(setId), getQuestions(setId), getProgress(setId)]);
        if (!set || qs.length === 0) {
          router.replace("/bank");
          return;
        }
        setSetInfo(set);
        setQuestions(qs);
        setAnswers(prog.answers);
        // 继续刷题：跳到第一题未作答
        const firstUnanswered = qs.findIndex((q) => !prog.answers[q.id]);
        setIndex(firstUnanswered === -1 ? 0 : firstUnanswered);
      }
      setLoaded(true);
    })();
  }, [setId, isReview, router]);

  // 计时
  useEffect(() => {
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const showToast = useCallback((msg: string) => {
    setToast(msg);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 2600);
  }, []);

  const question = questions[index];
  const answeredCount = useMemo(
    () => questions.filter((q) => answers[q.id]).length,
    [questions, answers],
  );

  const choose = async (letter: string) => {
    if (!question) return;
    if (isReview) {
      const correct = letter === question.answer;
      const r = await submitReview(question.id, correct);
      setAnswers((a) => ({ ...a, [question.id]: letter }));
      if (!correct) showToast("仍待巩固，已重置复习进度");
      if (r.mastered) showToast("连续答对，该题已归档「已掌握」");
      return;
    }
    const r = await submitAnswer(setId, question.id, letter);
    setAnswers((a) => ({ ...a, [question.id]: letter }));
    if (!r.correct && r.autoAddedToWrongBook) {
      setWrongAdded((m) => ({ ...m, [question.id]: true }));
      showToast("答错已自动收录进错题本，解析见下方");
    }
  };

  const handleAddWrong = async (reason: WrongReason) => {
    if (!question) return;
    await addToWrongBook(question.id, reason);
    setWrongAdded((m) => ({ ...m, [question.id]: true }));
    setReasonOpen(false);
    showToast(`已加入错题本（${WRONG_REASON_LABEL[reason]}）`);
  };

  // 已判分题目同步错题本收录状态，并随切题关闭错因弹层
  useEffect(() => {
    setReasonOpen(false);
    if (!question || !answers[question.id]) return;
    isInWrongBook(question.id).then((v) =>
      setWrongAdded((m) => ({ ...m, [question.id]: v })),
    );
  }, [index, question, answers]);

  const restart = async () => {
    await resetProgress(setId);
    setAnswers({});
    setIndex(0);
    setSeconds(0);
    showToast("已重置进度，重新开始");
  };

  if (!loaded || !setInfo) {
    return <div className="py-24 text-center text-sm text-muted">加载中…</div>;
  }

  // 复习模式空态：错题本为空 / 无到期错题
  if (!question) {
    return (
      <div className="card mx-auto max-w-3xl py-20 text-center">
        <p className="text-sm text-muted">太棒了，当前没有待复习的错题</p>
        <Link href="/bank" className="mt-3 inline-block text-sm font-medium text-brand">
          去刷一套题检验一下 →
        </Link>
      </div>
    );
  }

  const chosen = answers[question.id];
  const judged = chosen !== undefined;
  const allDone = answeredCount === questions.length;

  return (
    <div className="mx-auto max-w-3xl">
      {/* 顶栏：退出 / 题号 / 计时 */}
      <div className="mb-3 flex items-center justify-between">
        <button className="btn-secondary !px-3 !py-1.5 text-xs" onClick={() => router.push(isReview ? "/wrong-book" : "/bank")}>
          退出
        </button>
        <span className="text-sm text-muted">
          第 <span className="font-semibold text-ink">{index + 1}</span> / {questions.length} 题
        </span>
        <span className="flex items-center gap-1.5 text-sm text-muted">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="13" r="8" />
            <path d="M12 9v4l2.5 2.5M9 2h6" strokeLinecap="round" />
          </svg>
          {fmt(seconds)}
        </span>
      </div>
      <ProgressBar value={answeredCount} max={questions.length} />

      {/* 题目卡 */}
      <div className="card mt-4 p-6">
        <div className="mb-3 flex items-center gap-2">
          <span className={`tag ${isReview ? "bg-orange-50 text-warn" : SOURCE_TAG_CLASS[setInfo.source]}`}>
            {isReview ? "错题复习" : SOURCE_LABEL[setInfo.source]}
          </span>
          <span className="tag bg-bg text-muted">{question.category}</span>
          <span className="ml-auto flex items-center gap-1 text-xs text-muted">
            难度
            {Array.from({ length: 3 }).map((_, i) => (
              <span key={i} className={`h-1.5 w-3 rounded-full ${i < question.difficulty ? "bg-warn" : "bg-line"}`} />
            ))}
          </span>
        </div>

        <h2 className="text-base font-medium leading-7">{question.stem}</h2>

        {/* 选项 */}
        <div className="mt-5 space-y-2.5">
          {question.options.map((opt, i) => {
            const letter = OPTION_LETTERS[i];
            const isChosen = chosen === letter;
            const isAnswer = question.answer === letter;
            let cls = "border-line hover:border-brand";
            let letterCls = "bg-bg text-muted";
            if (judged) {
              if (isAnswer) {
                cls = "border-success bg-green-50";
                letterCls = "bg-success text-white";
              } else if (isChosen) {
                cls = "border-danger bg-red-50";
                letterCls = "bg-danger text-white";
              } else {
                cls = "border-line opacity-60";
              }
            } else if (isChosen) {
              cls = "border-brand bg-brand-light";
              letterCls = "bg-brand text-white";
            }
            return (
              <button
                key={letter}
                onClick={() => choose(letter)}
                className={`flex w-full items-center gap-3 rounded-btn border px-4 py-3 text-left text-sm transition-colors ${cls}`}
              >
                <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-medium ${letterCls}`}>
                  {letter}
                </span>
                <span>{opt}</span>
                {judged && isAnswer && (
                  <svg className="ml-auto shrink-0" width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <path d="M3 8.5L6.5 12L13 4.5" stroke="#1E9E6A" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </button>
            );
          })}
        </div>

        {/* 判定反馈 */}
        {judged && (
          <div
            className={`mt-4 flex items-center rounded-btn px-4 py-3 text-sm ${
              chosen === question.answer ? "bg-green-50 text-success" : "bg-red-50 text-danger"
            }`}
          >
            <span>
              {chosen === question.answer
                ? "回答正确"
                : `回答错误，正确答案：${question.answer}`}
            </span>
            <div className="ml-auto flex items-center gap-4">
              {wrongAdded[question.id] ? (
                <span className="text-xs opacity-80">已在错题本中</span>
              ) : (
                <div className="relative">
                  <button
                    className="font-medium underline underline-offset-2"
                    onClick={() => setReasonOpen((v) => !v)}
                  >
                    加入错题本
                  </button>
                  {reasonOpen && (
                    <div className="card absolute bottom-9 right-0 z-20 w-44 p-2 text-left shadow-lg">
                      <p className="mb-1 px-2 text-xs text-muted">选择错因（错因三分法）</p>
                      {(["concept", "misread", "blind_spot"] as WrongReason[]).map((r) => (
                        <button
                          key={r}
                          className="block w-full rounded-btn px-3 py-1.5 text-left text-sm text-ink hover:bg-bg"
                          onClick={() => handleAddWrong(r)}
                        >
                          {WRONG_REASON_LABEL[r]}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
              <Link
                href={`/question/${question.id}?from=${setId}`}
                className="font-medium text-brand underline underline-offset-2"
              >
                查看答案解析 →
              </Link>
            </div>
          </div>
        )}
      </div>

      {/* 底部操作 */}
      <div className="mt-4 flex items-center gap-3">
        <button className="btn-secondary" disabled={index === 0} onClick={() => setIndex((i) => i - 1)}>
          上一题
        </button>
        <button
          className="btn-secondary"
          disabled={index === questions.length - 1}
          onClick={() => setIndex((i) => i + 1)}
        >
          下一题
        </button>
        <div className="flex-1" />
        {!isReview && (
          <button className="btn-secondary" onClick={restart}>
            重新刷
          </button>
        )}
        <button className="btn-primary" onClick={() => setSheetOpen(true)}>
          答题卡
        </button>
      </div>

      {/* 答题卡抽屉 */}
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
            <p className="mb-4 text-xs text-muted">
              已答 {answeredCount} 题 · 未答 {questions.length - answeredCount} 题 · 交卷前可修改答案
            </p>
            <div className="grid grid-cols-8 gap-2">
              {questions.map((q, i) => {
                const answered = !!answers[q.id];
                // 直接比对正确答案，刷新/重进后依然保持绿/红着色
                const correct = answered && answers[q.id] === q.answer;
                let cls = "border-line bg-card text-muted";
                if (correct) cls = "border-success bg-green-50 text-success";
                else if (answered) cls = "border-danger bg-red-50 text-danger";
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
            {allDone && (
              <button
                className="btn-primary mt-5 w-full"
                onClick={() => router.push(isReview ? "/wrong-book" : "/bank")}
              >
                全部完成，返回{isReview ? "错题本" : "题库中心"}
              </button>
            )}
          </div>
        </div>
      )}

      {/* Toast */}
      {toast && (
        <div className="fixed bottom-8 left-1/2 z-50 -translate-x-1/2 rounded-full bg-ink px-5 py-2.5 text-sm text-white shadow-lg">
          {toast}
        </div>
      )}
    </div>
  );
}
