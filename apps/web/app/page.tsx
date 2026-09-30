"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { StatCard } from "@/components/ui";
import {
  addPlan,
  deletePlan,
  getDailyPractice,
  getMe,
  getPlans,
  getWeekOverview,
  togglePlan,
} from "@/lib/api";
import type { DailyPractice, MeProfile, PlanList, PlanTask, WeekOverview } from "@/lib/types";

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

const WEEKDAY = ["日", "一", "二", "三", "四", "五", "六"];

function greeting(): string {
  const h = new Date().getHours();
  if (h < 6) return "夜深了";
  if (h < 12) return "早上好";
  if (h < 18) return "下午好";
  return "晚上好";
}

/** 「开始今日刷题」目标：按计划顺序取第一个未完成任务的落地页 */
function planHref(p: PlanTask): string {
  if (p.type === "practice") return p.refId ? `/practice/${p.refId}` : "/bank";
  if (p.type === "review") return "/wrong-book";
  if (p.type === "resume_check") return "/resume";
  return "/bank";
}

export default function HomePage() {
  const [plan, setPlan] = useState<PlanList | null>(null);
  const [week, setWeek] = useState<WeekOverview | null>(null);
  const [me, setMe] = useState<MeProfile | null>(null);
  const [daily, setDaily] = useState<DailyPractice | null>(null);
  const [newPlan, setNewPlan] = useState("");
  const [toast, setToast] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    getPlans().then(setPlan).catch((e) => setError(e.message));
    getWeekOverview().then(setWeek).catch(() => {});
    getMe().then(setMe).catch(() => {}); // 问候用昵称，未登录静默
    // 每日一练（三期）：首访即触发后端惰性生成当日 10 题；未登录静默
    getDailyPractice().then(setDaily).catch(() => {});
  }, []);

  const maxBar = Math.max(...(week?.bars.map((b) => b.value) ?? [0]), 1);

  const apply = (next: PlanList, tip = "") => {
    setPlan(next);
    if (tip) {
      setToast(tip);
      setTimeout(() => setToast(""), 4000);
    }
  };

  const onToggle = async (id: string) => {
    try {
      const next = await togglePlan(id);
      apply(
        next,
        next.source === "checked_in"
          ? `打卡成功 · 已连续学习 ${next.streak} 天`
          : "",
      );
      // 打卡达成后 streak 变化，让顶栏徽标即时同步
      if (next.source === "checked_in") window.dispatchEvent(new Event("profile:refresh"));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const onAdd = async () => {
    const title = newPlan.trim();
    if (!title) return;
    try {
      apply(await addPlan(title));
      setNewPlan("");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const onDelete = async (id: string) => {
    try {
      apply(await deletePlan(id));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const firstTodo = plan?.items.find((p) => !p.done);

  return (
    <div className="space-y-5">
      {/* 问候区：日期 / streak 打卡横幅 / 开始今日刷题 */}
      <section className="card flex items-center justify-between p-6">
        <div>
          <h1 className="text-2xl font-semibold">
            {greeting()}，{me?.profile.name || "同学"}
          </h1>
          <p className="mt-1 text-sm text-muted">
            {new Date().toLocaleDateString("zh-CN", { month: "long", day: "numeric" })} 星期
            {WEEKDAY[new Date().getDay()]}
            {plan && plan.total > 0 && (
              <>
                {" · "}
                {plan.checkedIn ? "今日已打卡" : `今日已完成 ${plan.doneCount}/${plan.total} 项计划`}
                {" · "}
                <span className="font-medium text-brand">连续学习 {plan.streak} 天</span>
              </>
            )}
          </p>
        </div>
        {firstTodo ? (
          <Link href={planHref(firstTodo)} className="btn-primary">
            开始今日刷题
          </Link>
        ) : (
          <Link href="/bank" className="btn-primary">
            去题库逛逛
          </Link>
        )}
      </section>

      {toast && (
        <div className="card flex items-center gap-2 border-success/30 bg-success/5 px-4 py-3 text-sm text-success">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path d="M20 6L9 17l-5-5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          {toast}
        </div>
      )}
      {error && (
        <div className="card border-warn/30 bg-warn/5 px-4 py-3 text-sm text-warn">{error}</div>
      )}

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
        {/* 今日学习计划（二期：AI 生成 + 手动增删 + 打卡） */}
        <section className="card col-span-3 p-5">
          <div className="mb-3 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <h2 className="text-base font-semibold">今日学习计划</h2>
              {plan && plan.source === "ai" && (
                <span className="rounded-full bg-ai/10 px-2 py-0.5 text-xs font-medium text-ai">AI 规划</span>
              )}
              {plan && plan.source === "rule" && (
                <span className="rounded-full bg-brand-light px-2 py-0.5 text-xs font-medium text-brand">为你推荐</span>
              )}
            </div>
            <span className="text-sm text-muted">
              已完成 <span className="font-medium text-brand">{plan?.doneCount ?? 0}/{plan?.total ?? 0}</span>
            </span>
          </div>
          <ul className="divide-y divide-line">
            {(plan?.items ?? []).map((p) => (
              <li key={p.id} className="group flex items-center gap-3 py-2.5">
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
                {p.refId && !p.done ? (
                  <Link href={planHref(p)} className={`flex-1 text-sm hover:text-brand ${p.done ? "text-muted line-through" : "text-ink"}`}>
                    {p.title}
                  </Link>
                ) : (
                  <span className={`flex-1 text-sm ${p.done ? "text-muted line-through" : "text-ink"}`}>{p.title}</span>
                )}
                <span className="text-xs text-muted">（{p.estMinutes} 分钟）</span>
                <button
                  onClick={() => onDelete(p.id)}
                  className="hidden text-xs text-muted hover:text-warn group-hover:inline"
                  aria-label={`删除「${p.title}」`}
                >
                  删除
                </button>
              </li>
            ))}
          </ul>
          <div className="mt-3 flex gap-2">
            <input
              className="input !py-2"
              placeholder="＋ 添加学习计划"
              value={newPlan}
              maxLength={60}
              onChange={(e) => setNewPlan(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && onAdd()}
            />
            <button className="btn-secondary shrink-0 !py-2" onClick={onAdd}>
              添加
            </button>
          </div>
        </section>

        {/* 本周学习 + 统计（作答事件真实聚合） */}
        <div className="col-span-2 space-y-4">
          {/* 每日一练（三期）：薄弱 60% + 随机 40%，全部完成计入打卡 */}
          <section className="card p-5">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-base font-semibold">每日一练</h2>
                <p className="mt-0.5 text-xs text-muted">
                  今天 {daily?.total ?? 10} 题 · 薄弱知识点 60% + 随机 40%
                </p>
              </div>
              {daily && (
                <span className="text-sm text-muted">
                  已完成 <span className="font-medium text-brand">{daily.doneIds.length}/{daily.total}</span>
                </span>
              )}
            </div>
            <div className="mt-3 h-1.5 w-full rounded-full bg-line/60">
              <div
                className="h-full rounded-full bg-brand transition-all"
                style={{
                  width: `${
                    daily && daily.total > 0
                      ? Math.max((daily.doneIds.length / daily.total) * 100, daily.doneIds.length > 0 ? 4 : 0)
                      : 0
                  }%`,
                }}
              />
            </div>
            <div className="mt-3 flex items-center justify-between">
              <span className="text-xs text-muted">
                {daily && daily.checkedInToday
                  ? "今日已打卡"
                  : `完成全部题目即打卡 · 已连续 ${daily?.streak ?? 0} 天`}
              </span>
              <Link href="/daily" className="btn-secondary !py-2 text-sm">
                {daily && daily.doneIds.length >= daily.total ? "回看今日" : "去练习"}
              </Link>
            </div>
          </section>
          <section className="card p-5">
            <h2 className="mb-4 text-base font-semibold">本周学习</h2>
            <div className="flex h-28 items-end justify-between gap-2">
              {(week?.bars ?? []).map((b) => (
                <div key={b.date} className="flex flex-1 flex-col items-center gap-1.5">
                  <div
                    className={`w-full rounded-t-md ${
                      b.value === 0
                        ? "bg-line/60"
                        : b.value === maxBar
                          ? "bg-brand"
                          : "bg-brand-light"
                    }`}
                    style={{ height: `${Math.max((b.value / maxBar) * 96, b.value > 0 ? 4 : 6)}px` }}
                    title={`${b.date} · ${b.value} 题`}
                  />
                  <span className="text-xs text-muted">{b.day}</span>
                </div>
              ))}
            </div>
          </section>
          <section className="grid grid-cols-3 gap-3">
            <StatCard value={week?.stats.answered ?? 0} label="本周刷题" />
            <StatCard value={`${week?.stats.correctRate ?? 0}%`} label="正确率" tone="success" />
            <StatCard value={week?.stats.pendingReview ?? 0} label="待复习错题" tone="warn" />
          </section>
        </div>
      </div>
    </div>
  );
}
