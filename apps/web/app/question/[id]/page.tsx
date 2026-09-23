"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { SOURCE_TAG_CLASS } from "@/components/ui";
import {
  addToWrongBook,
  getProgress,
  getQuestion,
  getSet,
  isInWrongBook,
  toggleFavorite,
  getFavorites,
} from "@/lib/api";
import {
  SOURCE_LABEL,
  WRONG_REASON_LABEL,
  type Question,
  type QuestionSet,
  type WrongReason,
} from "@/lib/types";

const OPTION_LETTERS = ["A", "B", "C", "D", "E", "F"];
const REASONS: WrongReason[] = ["concept", "misread", "blind_spot"];

function QuestionDetail() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const router = useRouter();
  const from = searchParams.get("from");

  const [question, setQuestion] = useState<Question | null>(null);
  const [setInfo, setSetInfo] = useState<QuestionSet | null>(null);
  const [userChoice, setUserChoice] = useState<string | undefined>();
  const [tab, setTab] = useState<"explanation" | "reference">("explanation");
  const [favorite, setFavorite] = useState(false);
  const [inWrongBook, setInWrongBook] = useState(false);
  const [reasonPicker, setReasonPicker] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const q = await getQuestion(params.id);
      if (!q) {
        router.replace("/bank");
        return;
      }
      setQuestion(q);
      setSetInfo(await getSet(q.setId));
      setInWrongBook(await isInWrongBook(q.id));
      setFavorite((await getFavorites()).includes(q.id));
      const prog = await getProgress(q.setId);
      setUserChoice(prog.answers[q.id]);
    })();
  }, [params.id, router]);

  if (!question) {
    return <div className="py-24 text-center text-sm text-muted">加载中…</div>;
  }

  const notify = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 2400);
  };

  const onToggleFav = async () => {
    const now = await toggleFavorite(question.id);
    setFavorite(now);
    notify(now ? "已收藏" : "已取消收藏");
  };

  const onAddWrong = async (reason: WrongReason) => {
    await addToWrongBook(question.id, reason);
    setInWrongBook(true);
    setReasonPicker(false);
    notify(`已加入错题本（${WRONG_REASON_LABEL[reason]}）`);
  };

  return (
    <div className="mx-auto max-w-3xl">
      {/* 顶栏 */}
      <div className="mb-4 flex items-center justify-between">
        <button className="btn-secondary !px-3 !py-1.5 text-xs" onClick={() => router.back()}>
          ← 返回
        </button>
        <span className="text-sm font-medium">题目详情</span>
        <button onClick={onToggleFav} className="p-1" title={favorite ? "取消收藏" : "收藏"}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill={favorite ? "#E8833A" : "none"} stroke={favorite ? "#E8833A" : "#9AA0AB"} strokeWidth="1.8">
            <path d="M12 2l2.9 6.3 6.9.8-5.1 4.7 1.4 6.8-6.1-3.4-6.1 3.4 1.4-6.8L2.2 9.1l6.9-.8L12 2z" strokeLinejoin="round" />
          </svg>
        </button>
      </div>

      {/* 题面 */}
      <div className="card p-6">
        <div className="mb-3 flex items-center gap-2">
          {setInfo && <span className={`tag ${SOURCE_TAG_CLASS[setInfo.source]}`}>{SOURCE_LABEL[setInfo.source]}</span>}
          <span className="tag bg-bg text-muted">{question.category}</span>
          {question.knowledgeTags.map((t) => (
            <span key={t} className="tag bg-brand-light text-brand">{t}</span>
          ))}
        </div>
        <h1 className="text-base font-medium leading-7">{question.stem}</h1>

        {/* 答案信息 */}
        <div className="mt-4 flex items-center gap-3">
          <span className="tag bg-green-50 px-3 py-1 text-sm font-medium text-success">
            正确答案 {question.answer}
          </span>
          {question.siteCorrectRate !== null && (
            <span className="tag bg-bg px-3 py-1 text-sm text-muted">
              全站答对率 <span className="font-semibold text-ink">{question.siteCorrectRate}%</span>
            </span>
          )}
        </div>

        {/* 选项着色 */}
        <div className="mt-5 space-y-2.5">
          {question.options.map((opt, i) => {
            const letter = OPTION_LETTERS[i];
            const isAnswer = question.answer === letter;
            const isWrongPick = userChoice === letter && !isAnswer;
            return (
              <div
                key={letter}
                className={`flex items-center gap-3 rounded-btn border px-4 py-3 text-sm ${
                  isAnswer
                    ? "border-success bg-green-50"
                    : isWrongPick
                      ? "border-danger bg-red-50"
                      : "border-line opacity-70"
                }`}
              >
                <span
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-medium ${
                    isAnswer ? "bg-success text-white" : isWrongPick ? "bg-danger text-white" : "bg-bg text-muted"
                  }`}
                >
                  {letter}
                </span>
                <span>{opt}</span>
                {isAnswer && <span className="ml-auto text-xs text-success">正确项</span>}
                {isWrongPick && <span className="ml-auto text-xs text-danger">我的误选</span>}
              </div>
            );
          })}
        </div>

        {/* 双 tab */}
        <div className="mt-6 border-b border-line">
          {(
            [
              ["explanation", "答案解析"],
              ["reference", "参考回答"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`-mb-px mr-6 border-b-2 pb-2.5 text-sm transition-colors ${
                tab === key ? "border-brand font-medium text-brand" : "border-transparent text-muted hover:text-ink"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="py-4 text-sm leading-7 text-ink">
          {tab === "explanation" ? (
            <>
              <p>{question.explanation}</p>
              <p className="mt-3 text-xs text-muted">AI 生成，仅供参考 · 有疑问可点击右下角「反馈」</p>
            </>
          ) : (
            <>
              <p className="mb-2 font-medium">作答要点：</p>
              <p>{question.referenceAnswer}</p>
            </>
          )}
        </div>
      </div>

      {/* 底部操作 */}
      <div className="mt-4 flex items-center gap-3">
        {inWrongBook ? (
          <span className="tag bg-orange-50 px-4 py-2 text-sm text-warn">已在错题本中</span>
        ) : (
          <div className="relative">
            <button className="btn-primary" onClick={() => setReasonPicker((v) => !v)}>
              加入错题本
            </button>
            {reasonPicker && (
              <div className="card absolute bottom-12 left-0 z-10 w-56 p-3 shadow-lg">
                <p className="mb-2 text-xs text-muted">选择错因（错因三分法）</p>
                {REASONS.map((r) => (
                  <button
                    key={r}
                    className="block w-full rounded-btn px-3 py-2 text-left text-sm hover:bg-bg"
                    onClick={() => onAddWrong(r)}
                  >
                    {WRONG_REASON_LABEL[r]}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        <button className="btn-secondary ml-auto" onClick={() => notify("感谢反馈，我们会在 24h 内处理")}>
          反馈
        </button>
      </div>

      {toast && (
        <div className="fixed bottom-8 left-1/2 z-50 -translate-x-1/2 rounded-full bg-ink px-5 py-2.5 text-sm text-white shadow-lg">
          {toast}
        </div>
      )}
    </div>
  );
}

export default function QuestionDetailPage() {
  return (
    <Suspense fallback={<div className="py-24 text-center text-sm text-muted">加载中…</div>}>
      <QuestionDetail />
    </Suspense>
  );
}
