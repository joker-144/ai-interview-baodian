"use client";

/**
 * 简历体检报告（P12，产品文档 3.4 / 二期批 3）
 *
 * - 总分 + 评语、亮点 ✓ 清单、待改进 ！ 清单、AI 优化建议、能力维度条形；
 * - 「一键优化简历」：按体检结论主模型重写，完成后展示新旧对比（双栏）+ DOCX 下载；
 * - 版本切换：多版本简历共用本页（?v={resumeId} 直达，默认最新版本）。
 */

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { PageHeader, ProgressBar } from "@/components/ui";
import {
  deleteResume,
  downloadResumeDocx,
  getResumeCheckup,
  getResumes,
  optimizeResume,
} from "@/lib/api";
import type { ResumeCheckup, ResumeVersion } from "@/lib/types";

function ScoreRing({ score, label = "体检总分" }: { score: number; label?: string }) {
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
        <span className="block text-xs text-muted">{label}</span>
      </div>
    </div>
  );
}

function CheckupList({
  title, items, tone,
}: {
  title: string;
  items: string[];
  tone: "success" | "warn" | "brand";
}) {
  const toneCls =
    tone === "success" ? "text-success" : tone === "warn" ? "text-warn" : "text-brand";
  const icon = tone === "success" ? "✓" : tone === "warn" ? "！" : "◆";
  return (
    <div>
      <h3 className={`mb-2 text-sm font-semibold ${toneCls}`}>{title}</h3>
      {items.length === 0 ? (
        <p className="text-xs text-muted">暂无内容</p>
      ) : (
        <ul className="space-y-2">
          {items.map((item, i) => (
            <li key={`${i}-${item.slice(0, 8)}`} className="flex gap-2 text-sm leading-relaxed">
              <span className={`shrink-0 font-medium ${toneCls}`}>{icon}</span>
              <span className="text-ink">{item}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** 单个版本的体检卡（单栏展示与新旧对比双栏复用） */
function CheckupCard({ version, compact = false }: { version: ResumeVersion; compact?: boolean }) {
  const analysis = version.analysis;
  const checkup: ResumeCheckup | null | undefined = analysis?.checkup;
  if (!analysis || !checkup) return null;
  return (
    <div className={compact ? "card p-5" : ""}>
      <div className="mb-3 flex items-center gap-2">
        <span className="text-sm font-semibold">{analysis.fileName}</span>
        <span className={`tag ${version.isOptimized ? "bg-success/10 text-success" : "bg-line/70 text-muted"}`}>
          {version.isOptimized ? "优化版" : "原版"}
        </span>
        <span className="text-xs text-muted">v{version.version}</span>
      </div>
      <div className="flex items-start gap-4">
        <ScoreRing score={checkup.totalScore} />
        <div className="min-w-0 flex-1">
          <p className="text-sm leading-relaxed text-ink">{checkup.comment}</p>
          <div className={`mt-3 ${compact ? "" : "grid grid-cols-2 gap-x-6 gap-y-3"}`}>
            <CheckupList title="亮点" items={checkup.highlights} tone="success" />
            <CheckupList title="待改进" items={checkup.improvements} tone="warn" />
          </div>
        </div>
      </div>
    </div>
  );
}

function ReportInner() {
  const params = useSearchParams();
  const [versions, setVersions] = useState<ResumeVersion[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [optimizing, setOptimizing] = useState(false);
  const [optimizeError, setOptimizeError] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState("");
  /** 优化完成后的新旧对比（原版 -> 优化版） */
  const [compareOriginal, setCompareOriginal] = useState<ResumeVersion | null>(null);

  useEffect(() => {
    getResumes()
      .then((vs) => {
        setVersions(vs);
        if (vs.length) {
          const want = params.get("v");
          setCurrentId(vs.find((v) => v.resumeId === want)?.resumeId ?? vs[0].resumeId);
        }
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : "加载简历列表失败"))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const current = useMemo(
    () => versions.find((v) => v.resumeId === currentId) ?? null,
    [versions, currentId],
  );

  /** 旧记录缺 checkup：体检接口惰性补算（后端写回，这里只补本地展示态） */
  useEffect(() => {
    if (!currentId) return;
    const cur = versions.find((v) => v.resumeId === currentId);
    if (!cur?.analysis || cur.analysis.checkup) return;
    let alive = true;
    getResumeCheckup(currentId)
      .then((ck) => {
        if (!alive) return;
        setVersions((vs) =>
          vs.map((v) =>
            v.resumeId === currentId && v.analysis
              ? { ...v, analysis: { ...v.analysis, checkup: ck } }
              : v,
          ),
        );
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [currentId, versions]);

  const handleOptimize = async () => {
    if (!current) return;
    setOptimizing(true);
    setOptimizeError("");
    try {
      const optimized = await optimizeResume(current.resumeId);
      const vs = await getResumes();
      setVersions(vs);
      setCompareOriginal(current);
      setCurrentId(optimized.resumeId ?? vs[0]?.resumeId ?? null);
    } catch (err) {
      setOptimizeError(err instanceof Error ? err.message : "AI 优化失败，请重试");
    } finally {
      setOptimizing(false);
    }
  };

  const handleDownload = async () => {
    if (!current) return;
    setDownloading(true);
    setDownloadError("");
    try {
      const base = (current.analysis?.fileName || current.fileName || "简历").replace(/\.docx$/i, "");
      await downloadResumeDocx(current.resumeId, base);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : "下载失败，请重试");
    } finally {
      setDownloading(false);
    }
  };

  const handleDelete = async (resumeId: string) => {
    if (!window.confirm("确定删除该简历版本？删除后不可恢复")) return;
    try {
      await deleteResume(resumeId);
      const vs = await getResumes();
      setVersions(vs);
      if (currentId === resumeId) setCurrentId(vs[0]?.resumeId ?? null);
      if (compareOriginal?.resumeId === resumeId) setCompareOriginal(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "删除失败，请重试");
    }
  };

  if (loading) {
    return (
      <div>
        <PageHeader title="简历体检报告" sub="总分 · 亮点 · 待改进 · AI 优化建议" />
        <div className="card flex h-40 items-center justify-center text-sm text-muted">
          加载中…
        </div>
      </div>
    );
  }

  if (loadError) {
    return (
      <div>
        <PageHeader title="简历体检报告" sub="总分 · 亮点 · 待改进 · AI 优化建议" />
        <div className="card p-6 text-sm text-danger">{loadError}</div>
      </div>
    );
  }

  if (!versions.length || !current?.analysis) {
    return (
      <div>
        <PageHeader title="简历体检报告" sub="总分 · 亮点 · 待改进 · AI 优化建议" />
        <div className="card flex flex-col items-center justify-center gap-3 p-10 text-center">
          <p className="text-sm text-ink">还没有可体检的简历</p>
          <p className="text-xs text-muted">上传简历并完成 AI 解析后，这里会生成体检报告</p>
          <Link href="/resume" className="btn-primary mt-2">去上传简历</Link>
        </div>
      </div>
    );
  }

  const analysis = current.analysis;
  const checkup = analysis.checkup;
  const optimized = compareOriginal
    ? versions.find((v) => v.resumeId === currentId) ?? null
    : null;

  return (
    <div>
      <PageHeader
        title="简历体检报告"
        sub="总分 · 亮点 · 待改进 · AI 优化建议 · 一键优化生成新版"
      />

      {/* 版本切换（多版本时展示） */}
      {versions.length > 1 && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          {versions.map((v) => (
            <button
              key={v.resumeId}
              onClick={() => setCurrentId(v.resumeId)}
              className={`rounded-btn border px-3 py-1.5 text-xs transition-colors ${
                v.resumeId === currentId
                  ? "border-brand bg-brand-light/40 font-medium text-brand"
                  : "border-line text-muted hover:border-brand/40"
              }`}
            >
              v{v.version} · {v.isOptimized ? "优化版" : "原版"} · {v.analysis?.fileName ?? v.fileName}
            </button>
          ))}
        </div>
      )}

      {/* 优化完成后的新旧对比（双栏） */}
      {compareOriginal && optimized?.analysis?.checkup && (
        <section className="card mb-4 p-5">
          <h2 className="mb-3 text-base font-semibold">
            新旧对比 <span className="tag ml-2 bg-success/10 text-success">优化完成</span>
          </h2>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <CheckupCard version={compareOriginal} compact />
            <CheckupCard version={optimized} compact />
          </div>
        </section>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        {/* 左：总分 + 操作 */}
        <section className="card flex flex-col items-center p-6 lg:col-span-2">
          {checkup ? (
            <ScoreRing score={checkup.totalScore} />
          ) : (
            <div className="flex h-36 w-36 items-center justify-center rounded-full bg-bg text-xs text-muted">
              体检评分生成中…
            </div>
          )}
          <p className="mt-4 text-center text-sm leading-relaxed text-ink">
            {checkup?.comment ?? "正在调用主模型为这份简历评分…"}
          </p>
          <div className="mt-4 grid w-full grid-cols-2 gap-3">
            <div className="rounded-btn bg-bg p-3 text-center">
              <p className="text-xs text-muted">目标岗位</p>
              <p className="mt-1 text-sm font-semibold">{analysis.targetRole}</p>
            </div>
            <div className="rounded-btn bg-bg p-3 text-center">
              <p className="text-xs text-muted">工作年限</p>
              <p className="mt-1 text-sm font-semibold">
                {analysis.years > 0 ? `${analysis.years} 年` : "应届"}
              </p>
            </div>
          </div>

          <div className="mt-5 w-full space-y-2">
            {current.isOptimized ? (
              <p className="rounded-btn bg-success/10 px-3 py-2 text-center text-xs text-success">
                这是 AI 优化版，可下载 DOCX 直接使用
              </p>
            ) : (
              <button
                className="btn-primary w-full"
                onClick={handleOptimize}
                disabled={optimizing}
              >
                {optimizing ? "AI 优化中（约 10~30 秒）…" : "一键优化简历"}
              </button>
            )}
            <button
              className="btn-secondary w-full"
              onClick={handleDownload}
              disabled={downloading}
            >
              {downloading ? "生成 DOCX 中…" : "下载 DOCX"}
            </button>
            <button
              className="w-full text-xs text-muted underline-offset-2 transition-colors hover:text-danger hover:underline"
              onClick={() => handleDelete(current.resumeId)}
            >
              删除该版本
            </button>
          </div>
          {optimizeError && (
            <p className="mt-3 w-full rounded-btn bg-red-50 px-3 py-2 text-xs text-danger">{optimizeError}</p>
          )}
          {downloadError && (
            <p className="mt-3 w-full rounded-btn bg-red-50 px-3 py-2 text-xs text-danger">{downloadError}</p>
          )}
        </section>

        {/* 右：体检明细 */}
        <section className="card p-6 lg:col-span-3">
          {checkup ? (
            <div className="space-y-6">
              <CheckupList title="亮点" items={checkup.highlights} tone="success" />
              <CheckupList title="待改进" items={checkup.improvements} tone="warn" />
              <CheckupList title="AI 优化建议" items={checkup.suggestions} tone="brand" />
              <div>
                <h3 className="mb-3 text-sm font-semibold text-ink">能力维度评估</h3>
                <div className="space-y-3">
                  {analysis.dimensions.map((d) => (
                    <div key={d.label} className="flex items-center gap-3">
                      <span className="w-20 shrink-0 text-sm text-ink">{d.label}</span>
                      <div className="flex-1">
                        <ProgressBar
                          value={d.score}
                          max={100}
                          color={d.score >= 80 ? "bg-success" : d.score >= 70 ? "bg-brand" : "bg-warn"}
                        />
                      </div>
                      <span className="w-8 text-right text-sm font-medium">{d.score}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-muted">
              体检报告生成后展示亮点、待改进与优化建议
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

export default function ResumeReportPage() {
  return (
    <Suspense
      fallback={
        <div>
          <PageHeader title="简历体检报告" sub="总分 · 亮点 · 待改进 · AI 优化建议" />
          <div className="card flex h-40 items-center justify-center text-sm text-muted">加载中…</div>
        </div>
      }
    >
      <ReportInner />
    </Suspense>
  );
}
