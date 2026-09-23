import React from "react";

/** 页面头部：标题 + 副标题 */
export function PageHeader({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="mb-5 mt-2">
      <h1 className="text-2xl font-semibold tracking-wide">{title}</h1>
      {sub && <p className="mt-1 text-sm text-muted">{sub}</p>}
    </div>
  );
}

/** 统计数字卡 */
export function StatCard({
  value,
  label,
  tone = "ink",
}: {
  value: React.ReactNode;
  label: string;
  tone?: "ink" | "brand" | "success" | "warn" | "danger";
}) {
  const toneClass = {
    ink: "text-ink",
    brand: "text-brand",
    success: "text-success",
    warn: "text-warn",
    danger: "text-danger",
  }[tone];
  return (
    <div className="card flex flex-col items-center justify-center px-4 py-5">
      <span className={`text-2xl font-semibold ${toneClass}`}>{value}</span>
      <span className="mt-1 text-xs text-muted">{label}</span>
    </div>
  );
}

/** 进度条 */
export function ProgressBar({
  value,
  max,
  color = "bg-brand",
}: {
  value: number;
  max: number;
  color?: string;
}) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-line">
      <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

/** 来源徽章配色 */
export const SOURCE_TAG_CLASS: Record<string, string> = {
  resume: "bg-brand-light text-brand",
  job_search: "bg-orange-50 text-warn",
  jd_target: "bg-purple-50 text-ai",
  mock_interview: "bg-green-50 text-success",
};

/** 非一期功能占位页 */
export function ComingSoon({ title, phase, desc }: { title: string; phase: string; desc: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-28 text-center">
      <div className="card flex h-16 w-16 items-center justify-center">
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#014DB2" strokeWidth="1.6">
          <path d="M12 2l2.4 4.9 5.4.8-3.9 3.8.9 5.4-4.8-2.5-4.8 2.5.9-5.4L4.2 7.7l5.4-.8L12 2z" strokeLinejoin="round" />
        </svg>
      </div>
      <h1 className="mt-6 text-2xl font-semibold">{title}</h1>
      <p className="mt-2 max-w-md text-sm text-muted">{desc}</p>
      <span className="tag mt-5 bg-brand-light text-brand">{phase} 上线 · 当前为一期（基础学习闭环）</span>
    </div>
  );
}
