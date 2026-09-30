"use client";

/**
 * 每日一练轻量模式（三期，文档 3.8）：每天 10 题，薄弱知识点 60% + 随机 40%。
 *
 * 题目来自各题集（当日首次访问由后端惰性选定，快照不变），判分复用刷题链路
 * practice/submit（进度 / 错题 / 全站统计自然打通），本页只做逐题流式作答与打卡展示。
 */
import Link from "next/link";
import { useEffect, useState } from "react";

import { getDailyPractice, markDailyProgress, submitAnswer } from "@/lib/api";
import type { SubmitResult } from "@/lib/api";
import type { DailyPractice } from "@/lib/types";

const LETTERS = ["A", "B", "C", "D"];

export default function DailyPracticePage() {
  const [data, setData] = useState<DailyPractice | null>(null);
  const [error, setError] = useState("");
  const [index, setIndex] = useState(0);
  const [choice, setChoice] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<SubmitResult | null>(null);
  const [doneCount, setDoneCount] = useState(0);
  const [checkedIn, setCheckedIn] = useState(false);
  const [justCheckedIn, setJustCheckedIn] = useState(false);

  useEffect(() => {
    getDailyPractice()
      .then((d) => {
        setData(d);
        setDoneCount(d.doneIds.length);
        // 跳过已完成题，落在第一道未完成题上
        const firstTodo = d.questions.findIndex((q) => !d.doneIds.includes(q.id));
        setIndex(firstTodo === -1 ? d.questions.length : firstTodo);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "加载失败"));
  }, []);

  if (error) {
    return (
      <div className="card mx-auto mt-10 max-w-2xl p-6 text-sm text-warn">{error}</div>
    );
  }
  if (!data) {
    return <div className="mx-auto mt-10 max-w-2xl text-sm text-muted">加载中…</div>;
  }

  const questions = data.questions;
  const finished = index >= questions.length;
  const question = finished ? null : questions[index];

  const handleSubmit = async () => {
    if (!question || !choice || submitting) return;
    setSubmitting(true);
    try {
      // 判分走刷题原链路（传题目原 setId，进度/错题/统计自然打通）
      const res = await submitAnswer(question.setId, question.id, choice);
      setResult(res);
      const progress = await markDailyProgress(question.id);
      setDoneCount(progress.doneCount);
      if (progress.checkedIn) {
        setCheckedIn(true);
        setJustCheckedIn(true);
        // streak 变化，让顶栏徽标即时同步
        window.dispatchEvent(new Event("profile:refresh"));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "提交失败，请重试");
    } finally {
      setSubmitting(false);
    }
  };

  const handleNext = () => {
    setResult(null);
    setChoice("");
    setIndex((i) => i + 1);
  };

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <header className="card flex items-center justify-between p-5">
        <div>
          <h1 className="text-lg font-semibold">每日一练</h1>
          <p className="mt-0.5 text-sm text-muted">
            {data.date} · 薄弱知识点 60% + 随机 40% · 共 {data.total} 题
          </p>
        </div>
        <div className="text-right">
          <p className="text-sm font-medium text-brand">
            {doneCount}/{data.total} 题
          </p>
          <p className="text-xs text-muted">连续学习 {data.streak + (checkedIn ? 1 : 0)} 天</p>
        </div>
      </header>

      {justCheckedIn && (
        <div className="card flex items-center gap-2 border-success/30 bg-success/5 px-4 py-3 text-sm text-success">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path d="M20 6L9 17l-5-5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          今日一练全部完成，打卡成功！
        </div>
      )}

      {finished ? (
        <div className="card p-8 text-center">
          <p className="text-lg font-semibold">
            {doneCount >= data.total ? "今日一练已完成" : "今日剩余题目已清空"}
          </p>
          <p className="mt-1 text-sm text-muted">
            完成 {doneCount}/{data.total} 题
            {data.streak + (checkedIn ? 1 : 0) > 0 && ` · 连续学习 ${data.streak + (checkedIn ? 1 : 0)} 天`}
          </p>
          <div className="mt-4 flex justify-center gap-3">
            <Link href="/" className="btn-secondary">
              回首页
            </Link>
            <Link href="/wrong-book" className="btn-primary">
              去错题本复习
            </Link>
          </div>
        </div>
      ) : (
        question && (
          <section className="card p-5">
            <div className="flex items-center justify-between">
              <span className="rounded-full bg-brand-light px-2 py-0.5 text-xs font-medium text-brand">
                第 {index + 1} 题
              </span>
              <span className="text-xs text-muted">难度 {question.difficulty}/3</span>
            </div>
            <h2 className="mt-3 leading-7">{question.stem}</h2>
            <ul className="mt-4 space-y-2">
              {question.options.map((option, optionIndex) => {
                const letter = LETTERS[optionIndex];
                const selected = choice === letter;
                const isAnswer = result && result.answer === letter;
                const isWrongPick = result && selected && !result.correct;
                return (
                  <li key={letter}>
                    <button
                      type="button"
                      disabled={!!result || submitting}
                      onClick={() => setChoice(letter)}
                      className={`flex w-full items-start gap-2.5 rounded-btn border px-3 py-2.5 text-left text-sm transition-colors ${
                        isAnswer
                          ? "border-success bg-success/5"
                          : isWrongPick
                            ? "border-danger bg-danger/5"
                            : selected
                              ? "border-brand bg-brand-light/60"
                              : "border-line hover:border-brand"
                      }`}
                    >
                      <span className="font-medium">{letter}.</span>
                      <span className="flex-1">{option}</span>
                    </button>
                  </li>
                );
              })}
            </ul>

            {result && (
              <div
                className={`mt-4 rounded-btn p-3 text-sm ${
                  result.correct
                    ? "bg-success/5 text-success"
                    : "bg-danger/5 text-danger"
                }`}
              >
                <p className="font-medium">
                  {result.correct ? "回答正确" : `回答错误 · 正确答案 ${result.answer}`}
                  {result.autoAddedToWrongBook && " · 已自动加入错题本"}
                </p>
                {question.explanation && (
                  <p className="mt-1.5 leading-6 text-ink">解析：{question.explanation}</p>
                )}
                {question.referenceAnswer && (
                  <p className="mt-1.5 leading-6 text-muted">参考回答：{question.referenceAnswer}</p>
                )}
              </div>
            )}

            <div className="mt-4 flex justify-end gap-3">
              {!result ? (
                <button
                  type="button"
                  className="btn-primary"
                  onClick={handleSubmit}
                  disabled={!choice || submitting}
                >
                  {submitting ? "提交中…" : "提交答案"}
                </button>
              ) : (
                <button type="button" className="btn-primary" onClick={handleNext}>
                  {index + 1 >= questions.length ? "完成今日一练" : "下一题"}
                </button>
              )}
            </div>
          </section>
        )
      )}
    </div>
  );
}
