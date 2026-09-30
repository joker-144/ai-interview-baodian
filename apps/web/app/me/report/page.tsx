"use client";

/**
 * 学习报告（P14 二期，产品文档 8.3 路由映射）
 *
 * GET /api/reports/weekly：近 7 天刷题量/正确率趋势、本周掌握数（错题毕业累计）、
 * 薄弱知识点 TOP5、AI 下周建议（每周首次访问惰性生成并按周缓存，失败规则兜底）。
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { PageHeader, StatCard } from "@/components/ui";
import { getWeeklyReport } from "@/lib/api";
import type { WeeklyReport } from "@/lib/types";

export default function WeeklyReportPage() {
  const [report, setReport] = useState<WeeklyReport | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getWeeklyReport()
      .then(setReport)
      .catch((e) => setError(e instanceof Error ? e.message : "加载失败"));
  }, []);

  const maxAnswered = Math.max(...(report?.trend.map((t) => t.answered) ?? [0]), 1);
  const maxWrong = Math.max(...(report?.weakPoints.map((w) => w.wrongCount) ?? [0]), 1);

  return (
    <div>
      <PageHeader title="学习报告" sub="近 7 天学习趋势、薄弱知识点与 AI 下周建议" />

      {error && <div className="card border-warn/30 bg-warn/5 px-4 py-3 text-sm text-warn">{error}</div>}

      {!report && !error && (
        <div className="py-28 text-center text-sm text-muted">正在生成报告…</div>
      )}

      {report && (
        <>
          {/* 三卡：本周作答 / 正确率 / 累计掌握 */}
          <div className="mb-4 grid grid-cols-1 gap-4 md:grid-cols-3">
            <StatCard value={report.answered} label="近 7 天刷题" tone="brand" />
            <StatCard value={`${report.correctRate}%`} label="近 7 天正确率" tone="success" />
            <StatCard value={report.masteredTotal} label="累计掌握错题" tone="warn" />
          </div>

          {/* 趋势图：柱为作答量，柱下标注当日正确率（无作答日不标注） */}
          <section className="card mb-4 p-5">
            <div className="mb-4 flex items-center gap-2">
              <h2 className="text-base font-semibold">学习趋势</h2>
              <span className="tag bg-line/70 text-muted">{report.weekKey}</span>
            </div>
            <div className="flex h-40 items-end justify-between gap-2">
              {report.trend.map((t) => (
                <div key={t.date} className="flex flex-1 flex-col items-center gap-1.5">
                  <span className="text-xs text-muted">{t.answered > 0 ? t.answered : ""}</span>
                  <div
                    className={`w-full rounded-t-md ${
                      t.answered === 0
                        ? "bg-line/60"
                        : t.answered === maxAnswered
                          ? "bg-brand"
                          : "bg-brand-light"
                    }`}
                    style={{ height: `${Math.max((t.answered / maxAnswered) * 104, t.answered > 0 ? 4 : 6)}px` }}
                    title={`${t.date} · ${t.answered} 题`}
                  />
                  <span className="text-xs text-muted">{t.date.slice(5)}</span>
                  <span className={`text-[11px] ${t.correctRate === null ? "text-transparent" : "text-success"}`}>
                    {t.correctRate === null ? "0" : `${t.correctRate}%`}
                  </span>
                </div>
              ))}
            </div>
          </section>

          <div className="grid grid-cols-5 gap-4">
            {/* 薄弱知识点 TOP5（未掌握错题按题目标签聚合） */}
            <section className="card col-span-3 p-5">
              <h2 className="mb-4 text-base font-semibold">薄弱知识点 TOP5</h2>
              {report.weakPoints.length === 0 ? (
                <p className="py-6 text-center text-sm text-muted">
                  暂无未掌握错题，继续保持！去
                  <Link href="/bank" className="text-brand hover:underline">
                    题库
                  </Link>
                  刷题吧
                </p>
              ) : (
                <ul className="flex flex-col gap-3">
                  {report.weakPoints.map((w, i) => (
                    <li key={w.tag} className="flex items-center gap-3">
                      <span className="w-5 shrink-0 text-center text-xs font-medium text-muted">{i + 1}</span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate text-sm text-ink">{w.tag}</span>
                          <span className="shrink-0 text-xs text-warn">{w.wrongCount} 题待攻克</span>
                        </div>
                        <div className="mt-1.5 h-1.5 w-full rounded-full bg-line/70">
                          <div
                            className="h-full rounded-full bg-warn/70"
                            style={{ width: `${Math.max((w.wrongCount / maxWrong) * 100, 8)}%` }}
                          />
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {/* AI 下周建议（每周一次惰性生成，LLM 失败走规则兜底） */}
            <section className="card col-span-2 p-5">
              <div className="mb-3 flex items-center gap-2">
                <h2 className="text-base font-semibold">下周建议</h2>
                <span className="rounded-full bg-ai/10 px-2 py-0.5 text-xs font-medium text-ai">AI 生成</span>
              </div>
              <p className="whitespace-pre-wrap text-sm leading-7 text-ink/85">{report.suggestion}</p>
            </section>
          </div>
        </>
      )}
    </div>
  );
}
