"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { StatCard } from "@/components/ui";
import { addPlan, getPlans, getUser, getWeekOverview, togglePlan } from "@/lib/api";
import type { PlanTask, UserProfile } from "@/lib/types";

const ENGINES = [
  {
    href: "/resume",
    title: "简历 AI 分析出题",
    desc: "上传简历，AI 深度解析你的经历，生成覆盖广、有深度、贴合真实面试场景的专属题库",
    iconBg: "bg-brand-light text-brand",
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8l-6-6z" strokeLinejoin="round" />
        <path d="M14 2v6h6M9 13h6M9 17h4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    href: "/jobs",
    title: "岗位检索出题",
    desc: "输入目标岗位，实时检索 Boss 直聘在招职位，围绕真实岗位要求生成面试题",
    iconBg: "bg-orange-50 text-warn",
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <circle cx="11" cy="11" r="7" />
        <path d="M21 21l-4.35-4.35" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    href: "/jd",
    title: "JD 精准定制",
    desc: "粘贴岗位链接或截图，提取 JD 与公司背景，结合你的简历精准定制题目",
    iconBg: "bg-purple-50 text-ai",
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <circle cx="12" cy="12" r="9" />
        <circle cx="12" cy="12" r="5" />
        <circle cx="12" cy="12" r="1.5" fill="currentColor" />
      </svg>
    ),
  },
];

export default function HomePage() {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [plans, setPlans] = useState<PlanTask[]>([]);
  const [bars, setBars] = useState<{ day: string; value: number }[]>([]);
  const [stats, setStats] = useState({ answered: 0, correctRate: 0, pendingReview: 0 });
  const [newPlan, setNewPlan] = useState("");

  useEffect(() => {
    getUser().then(setUser);
    getPlans().then(setPlans);
    getWeekOverview().then((w) => {
      setBars(w.bars);
      setStats(w.stats);
    });
  }, []);

  const doneCount = plans.filter((p) => p.done).length;
  const maxBar = Math.max(...bars.map((b) => b.value), 1);

  const onToggle = async (id: string) => setPlans(await togglePlan(id));
  const onAdd = async () => {
    const title = newPlan.trim();
    if (!title) return;
    setPlans(await addPlan(title));
    setNewPlan("");
  };

  return (
    <div className="space-y-5">
      {/* 问候区 */}
      <section className="card flex items-center justify-between p-6">
        <div>
          <h1 className="text-2xl font-semibold">晚上好，{user?.name ?? "同学"}</h1>
          <p className="mt-1 text-sm text-muted">
            9月22日 星期二 · 今天也要向着心仪 Offer 更进一步
          </p>
        </div>
        <Link href="/bank" className="btn-primary">
          开始今日刷题
        </Link>
      </section>

      {/* 三大引擎入口 */}
      <section className="grid grid-cols-3 gap-4">
        {ENGINES.map((e) => (
          <Link key={e.href} href={e.href} className="card group p-5 transition-shadow hover:shadow-md">
            <span className={`flex h-11 w-11 items-center justify-center rounded-btn ${e.iconBg}`}>
              {e.icon}
            </span>
            <h2 className="mt-3 text-base font-semibold">{e.title}</h2>
            <p className="mt-1 text-sm leading-6 text-muted">{e.desc}</p>
            <span className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-brand">
              立即生成
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="transition-transform group-hover:translate-x-0.5">
                <path d="M5 12h14M13 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
          </Link>
        ))}
      </section>

      <div className="grid grid-cols-5 gap-4">
        {/* 今日学习计划 */}
        <section className="card col-span-3 p-5">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-semibold">今日学习计划</h2>
            <span className="text-sm text-muted">
              已完成 <span className="font-medium text-brand">{doneCount}/{plans.length}</span>
            </span>
          </div>
          <ul className="divide-y divide-line">
            {plans.map((p) => (
              <li key={p.id} className="flex items-center gap-3 py-2.5">
                <button
                  onClick={() => onToggle(p.id)}
                  className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border transition-colors ${
                    p.done ? "border-success bg-success text-white" : "border-line bg-card hover:border-brand"
                  }`}
                  aria-label={p.done ? "标记未完成" : "标记完成"}
                >
                  {p.done && (
                    <svg width="11" height="11" viewBox="0 0 16 16" fill="none">
                      <path d="M3 8.5L6.5 12L13 4.5" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  )}
                </button>
                <span className={`flex-1 text-sm ${p.done ? "text-muted line-through" : "text-ink"}`}>
                  {p.title}
                </span>
                <span className="text-xs text-muted">（{p.estMinutes} 分钟）</span>
              </li>
            ))}
          </ul>
          <div className="mt-3 flex gap-2">
            <input
              className="input !py-2"
              placeholder="＋ 添加学习计划"
              value={newPlan}
              onChange={(e) => setNewPlan(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && onAdd()}
            />
            <button className="btn-secondary shrink-0 !py-2" onClick={onAdd}>
              添加
            </button>
          </div>
        </section>

        {/* 本周学习 + 统计 */}
        <div className="col-span-2 space-y-4">
          <section className="card p-5">
            <h2 className="mb-4 text-base font-semibold">本周学习</h2>
            <div className="flex h-28 items-end justify-between gap-2">
              {bars.map((b) => (
                <div key={b.day} className="flex flex-1 flex-col items-center gap-1.5">
                  <div
                    className={`w-full rounded-t-md ${b.value === maxBar ? "bg-brand" : "bg-brand-light"}`}
                    style={{ height: `${(b.value / maxBar) * 96}px` }}
                    title={`${b.value} 题`}
                  />
                  <span className="text-xs text-muted">{b.day}</span>
                </div>
              ))}
            </div>
          </section>
          <section className="grid grid-cols-3 gap-3">
            <StatCard value={stats.answered} label="本周刷题" />
            <StatCard value={`${stats.correctRate}%`} label="正确率" tone="success" />
            <StatCard value={stats.pendingReview} label="待复习错题" tone="warn" />
          </section>
        </div>
      </div>
    </div>
  );
}
