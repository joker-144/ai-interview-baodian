"use client";

/**
 * 模考报告（P11，二期批 2）：/exam/report/[examId]
 *
 * SVG 分数圆环 + 维度分条形 + 正确率/总用时 + 同岗位分桶百分位
 * （样本 <5 显示「样本积累中」）+ 薄弱知识点 TOP5 + 一键补强 + 历史趋势。
 */

import { useRouter } from "next/navigation";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { PageHeader } from "@/components/ui";
import { getExamReport, remedialExam } from "@/lib/api";
import type { ExamReport } from "@/lib/types";

function fmt(sec: number) {
  const m = Math.floor(sec / 60);
  return m > 0 ? `${m} 分 ${sec % 60} 秒` : `${sec} 秒`;
}

/** SVG 分数圆环：score 0~100 → 圆弧 */
function ScoreRing({ score }: { score: number }) {
  const r = 54;
  const c = 2 * Math.PI * r;
  const filled = (Math.min(Math.max(score, 0), 100) / 100) * c;
  const tone = score >= 80 ? "#1E9E6A" : score >= 60 ? "#3B82F6" : "#E5484D";
  return (
    <div className="relative flex h-36 w-36 items-center justify-center">
      <svg width="144" height="144" viewBox="0 0 144 144" className="-rotate-90">
        <circle cx="72" cy="72" r={r} fill="none" stroke="#EDEDED" strokeWidth="12" />
        <circle
          cx="72" cy="72" r={r} fill="none" stroke={tone} strokeWidth="12" strokeLinecap="round"
          strokeDasharray={`${filled} ${c - filled}`}
        />
      </svg>
      <div className="absolute text-center">
        <span className="text-4xl font-semibold" style={{ color: tone }}>{score}</span>
        <span className="block text-xs text-muted">百分制得分</span>
      </div>
    </div>
  );
}

export default function ExamReportPage() {
  const router = useRouter();
  const params = useParams<{ examId: string }>();
  const examId = params.examId;

  const [report, setReport] = useState<ExamReport | null>(null);
  const [error, setError] = useState("");
  const [remedialBusy, setRemedialBusy] = useState(false);
  const [remedialDone, setRemedialDone] = useState<{ setId: string; title: string } | null>(null);

  useEffect(() => {
    getExamReport(examId)
      .then(setReport)
      .catch((e) => setError(e instanceof Error ? e.message : "加载报告失败"));
  }, [examId]);

  const onRemedial = async () => {
    setRemedialBusy(true);
    setError("");
    try {
      const r = await remedialExam(examId);
      setRemedialDone({ setId: r.setId, title: r.title });
    } catch (e) {
      setError(e instanceof Error ? e.message : "补强卷生成失败");
    } finally {
      setRemedialBusy(false);
    }
  };

  if (error && !report) {
    return <div className="card border-warn/30 bg-warn/5 mx-auto mt-10 max-w-xl px-4 py-3 text-sm text-warn">{error}</div>;
  }
  if (!report) {
    return <div className="py-24 text-center text-sm text-muted">正在生成报告…</div>;
  }

  return (
    <div>
      <PageHeader
        title="模考报告"
        sub={`岗位分桶：${report.bucketRole} · 交卷于 ${report.finishedAt ?? "—"}`}
      />

      {error && <div className="card border-warn/30 bg-warn/5 mb-4 px-4 py-3 text-sm text-warn">{error}</div>}

      <div className="grid grid-cols-5 gap-4">
        {/* 左：分数圆环 + 概览 + 百分位 */}
        <div className="col-span-2 space-y-4">
          <section className="card flex flex-col items-center p-6">
            <ScoreRing score={report.score} />
            <div className="mt-4 grid w-full grid-cols-2 gap-3 text-center">
              <div>
                <p className="text-lg font-semibold text-ink">{report.correctRate}%</p>
                <p className="text-xs text-muted">正确率（{report.correct}/{report.answered}）</p>
              </div>
              <div>
                <p className="text-lg font-semibold text-ink">{fmt(report.durationSec)}</p>
                <p className="text-xs text-muted">
                  总用时{report.pausedSec > 0 ? " · 含暂停" : ""}
                </p>
              </div>
            </div>
            <div className="mt-4 w-full rounded-btn bg-bg px-4 py-3 text-center">
              {report.percentile === null ? (
                <>
                  <p className="text-sm font-medium text-ink">样本积累中</p>
                  <p className="mt-0.5 text-xs text-muted">
                    同岗位已交卷 {report.sampleSize} 份，满 5 份后开放百分位对比
                  </p>
                </>
              ) : (
                <>
                  <p className="text-sm font-medium text-brand">
                    超越同岗位 {report.percentile}% 的考生
                  </p>
                  <p className="mt-0.5 text-xs text-muted">基于 {report.sampleSize} 份「{report.bucketRole}」分桶交卷记录</p>
                </>
              )}
            </div>
          </section>

          {/* 维度分：按 category 百分制条形 */}
          <section className="card p-5">
            <h2 className="mb-3 text-base font-semibold">维度得分</h2>
            {report.dimensionScores.length === 0 ? (
              <p className="py-4 text-center text-sm text-muted">本次未作答任何题目</p>
            ) : (
              <ul className="space-y-3">
                {report.dimensionScores.map((d) => (
                  <li key={d.label}>
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-ink">{d.label}</span>
                      <span className="text-muted">{d.score} 分（{d.correct}/{d.total}）</span>
                    </div>
                    <div className="mt-1.5 h-1.5 w-full rounded-full bg-line/70">
                      {/* 绝对百分制：条宽 = 得分占 100 分比例（避免单维度时相对最高分恒满宽） */}
                      <div
                        className={`h-full rounded-full ${d.score >= 80 ? "bg-success" : d.score >= 60 ? "bg-brand" : "bg-warn"}`}
                        style={{ width: `${Math.max(d.score, 4)}%` }}
                      />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        {/* 右：薄弱点 + 补强 + 历史趋势 */}
        <div className="col-span-3 space-y-4">
          <section className="card p-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-base font-semibold">薄弱知识点 TOP5</h2>
              <button className="btn-primary !px-4 !py-1.5 text-xs" onClick={onRemedial} disabled={remedialBusy}>
                {remedialBusy ? "组卷中…" : "一键补强"}
              </button>
            </div>
            {report.weakPoints.length === 0 ? (
              <p className="py-4 text-center text-sm text-muted">
                {report.answered === 0 ? "本次未作答任何题目，无法定位薄弱点" : "本次考试全部答对，非常扎实！"}
              </p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {report.weakPoints.map((w) => (
                  <span key={w.tag} className="tag bg-warn/10 text-warn">
                    {w.tag} · {w.wrongCount} 题
                  </span>
                ))}
              </div>
            )}
            {remedialDone && (
              <div className="mt-4 flex items-center gap-3 rounded-btn bg-green-50 px-4 py-3 text-sm text-success">
                <span className="flex-1">
                  补强卷「{remedialDone.title}」已生成，共 10 题以内同知识点题目
                </span>
                <button
                  className="btn-primary !px-3.5 !py-1.5 text-xs"
                  onClick={() => router.push(`/practice/${remedialDone.setId}`)}
                >
                  开始补强练习
                </button>
              </div>
            )}
          </section>

          <section className="card p-5">
            <h2 className="mb-3 text-base font-semibold">历史成绩</h2>
            {report.history.length <= 1 ? (
              <p className="py-4 text-center text-sm text-muted">这是你的第一份模考成绩，再考一场看趋势</p>
            ) : (
              <ul className="divide-y divide-line">
                {[...report.history].reverse().map((h, i, arr) => {
                  const prev = i > 0 ? arr[i - 1].score : null;
                  const diff = h.score !== null && prev !== null ? h.score - prev : null;
                  return (
                    <li key={h.examId} className="flex items-center gap-3 py-2.5 text-sm">
                      <span className="w-36 shrink-0 text-xs text-muted">{h.finishedAt ?? "—"}</span>
                      <span className="font-semibold text-ink">{h.score} 分</span>
                      {diff !== null && diff !== 0 && (
                        <span className={`text-xs ${diff > 0 ? "text-success" : "text-warn"}`}>
                          {diff > 0 ? "↑" : "↓"} {Math.abs(diff)}
                        </span>
                      )}
                      {h.examId === report.examId && (
                        <span className="tag ml-auto shrink-0 bg-brand-light text-brand">本次</span>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <div className="flex gap-3">
            <button className="btn-secondary flex-1" onClick={() => router.push("/bank")}>
              返回题库再考一场
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
