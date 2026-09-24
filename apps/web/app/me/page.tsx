"use client";

/**
 * 我的 / 设置（P14 一期最小版，产品文档 6.2 一期范围、8.3 页面清单）
 *
 * 一期落地：个人资料、三统计卡、复习提醒时间、退出登录、注销申请（7 天冷静期 + 数据清理）。
 * 二期补齐：学习报告入口、简历管理·N 份、通知管理、数据导出（与其承载功能同期上线）。
 * 注销是第五章合规「提供一键删除全部数据」的兑现入口，故不可后置到二期。
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  cancelDeactivation,
  executeDeactivation,
  getMe,
  isLoggedIn,
  logout,
  requestDeactivation,
  updateProfile,
  updateSettings,
} from "@/lib/api";
import { PageHeader, StatCard } from "@/components/ui";
import { DELETION_SCOPES } from "@/lib/mock-data";
import type { MeProfile } from "@/lib/types";

/** 二期项：只列出入口与说明，不做空壳页面 */
const PHASE2_ITEMS = [
  { label: "学习报告", desc: "周报与能力趋势入口" },
  { label: "简历管理", desc: "多份简历与各版本体检报告" },
  { label: "通知管理", desc: "站内信与浏览器通知偏好" },
  { label: "数据导出", desc: "导出个人数据副本" },
];

export default function MePage() {
  const router = useRouter();
  const [authed, setAuthed] = useState(false);
  const [me, setMe] = useState<MeProfile | null>(null);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ name: "", targetRole: "", years: "0" });
  const [remindTime, setRemindTime] = useState("20:00");
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  // 注销二次确认：需手动输入「注销」才可提交，避免误触不可逆操作
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [reason, setReason] = useState("");

  const notify = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 2600);
  };

  const load = useCallback(async () => {
    setAuthed(isLoggedIn());
    const data = await getMe();
    setMe(data);
    setRemindTime(data.settings.reviewReminderTime);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const run = async (key: string, fn: () => Promise<void>) => {
    setBusy(key);
    try {
      await fn();
    } catch (e) {
      notify(e instanceof Error ? e.message : "操作失败，请重试");
    } finally {
      setBusy(null);
    }
  };

  const startEdit = () => {
    if (!me) return;
    setForm({
      name: me.profile.name,
      targetRole: me.profile.targetRole,
      years: String(me.profile.years),
    });
    setEditing(true);
  };

  const onSaveProfile = () =>
    run("profile", async () => {
      const years = Number(form.years);
      if (!form.name.trim()) {
        notify("姓名不能为空");
        return;
      }
      if (!Number.isFinite(years) || years < 0 || years > 50) {
        notify("工作年限需在 0~50 之间");
        return;
      }
      const next = await updateProfile({
        name: form.name.trim(),
        targetRole: form.targetRole.trim(),
        years,
      });
      setMe(next);
      setEditing(false);
      notify("资料已更新");
    });

  const onToggleReminder = (enabled: boolean) =>
    run("reminder", async () => {
      setMe(await updateSettings({ reviewReminderEnabled: enabled }));
      notify(enabled ? "已开启复习提醒" : "已关闭复习提醒");
    });

  const onSaveReminder = () =>
    run("reminder", async () => {
      setMe(await updateSettings({ reviewReminderTime: remindTime }));
      notify(`复习提醒时间已设为 ${remindTime}`);
    });

  const onLogout = () =>
    run("logout", async () => {
      await logout();
      router.push("/login");
    });

  const onDeactivate = () =>
    run("deactivate", async () => {
      await requestDeactivation(reason);
      setMe(await getMe());
      setConfirmOpen(false);
      setConfirmText("");
      setReason("");
      notify("注销申请已提交，7 天冷静期内可撤回");
    });

  const onCancelDeactivation = () =>
    run("cancel", async () => {
      setMe(await cancelDeactivation());
      notify("已撤回注销申请，数据保持原样");
    });

  /** 冷静期到期后由定时任务触发；此处提供手动入口以验证清理链路（对齐后端 force 参数） */
  const onExecuteDeactivation = () =>
    run("execute", async () => {
      const result = await executeDeactivation(true);
      setMe(await getMe());
      notify(result.detail);
    });

  if (!me) {
    return <div className="py-28 text-center text-sm text-muted">正在加载…</div>;
  }

  if (!authed) {
    return (
      <div className="mx-auto flex max-w-sm flex-col justify-center py-24">
        <div className="card p-7 text-center">
          <h1 className="text-lg font-semibold">尚未登录</h1>
          <p className="mt-2 text-sm text-muted">登录后可查看个人资料、学习统计与账号设置。</p>
          <Link href="/login" className="btn-primary mt-5 block w-full">
            去登录
          </Link>
        </div>
      </div>
    );
  }

  const { profile, settings, stats, counts, deactivation } = me;
  const cooling = deactivation?.status === "cooling_off";
  const executed = deactivation?.status === "executed";

  return (
    <div>
      <PageHeader title="我的" sub="账号资料、学习统计、复习提醒与数据管理" />

      {/* 注销态横幅：冷静期可撤回，已执行则不可恢复 */}
      {cooling && deactivation && (
        <div className="card mb-4 border-warn/30 bg-orange-50 p-4">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="tag bg-warn/15 text-warn">注销冷静期</span>
            <p className="text-sm text-ink">
              已于 {deactivation.requestedAt} 提交注销申请，剩余{" "}
              <strong className="text-warn">{deactivation.remainingDays}</strong> 天（至{" "}
              {deactivation.coolingOffUntil}）
            </p>
            <div className="ml-auto flex gap-2">
              <button
                className="btn-secondary !px-3.5 !py-1.5 text-xs"
                onClick={onCancelDeactivation}
                disabled={busy !== null}
              >
                {busy === "cancel" ? "撤回中…" : "撤回申请"}
              </button>
              <button
                className="btn-secondary !px-3.5 !py-1.5 text-xs text-danger"
                onClick={onExecuteDeactivation}
                disabled={busy !== null}
                title="冷静期到期后由定时任务执行；此处为验证清理链路的手动入口"
              >
                {busy === "execute" ? "清理中…" : "立即清理数据"}
              </button>
            </div>
          </div>
          <p className="mt-2 text-xs leading-relaxed text-muted">
            冷静期内账号可正常使用，数据不会被删除；逾期未撤回将自动清理且不可恢复。
          </p>
        </div>
      )}
      {executed && (
        <div className="card mb-4 border-danger/30 bg-red-50 p-4">
          <span className="tag bg-danger/10 text-danger">账号已注销</span>
          <p className="mt-2 text-sm text-ink">
            全部个人数据已完成清理，不可恢复。如需继续使用请重新注册登录。
          </p>
        </div>
      )}

      {/* 个人资料 */}
      <section className="card mb-4 p-5">
        <header className="flex items-center gap-4">
          <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-brand text-xl text-white">
            {profile.avatarText}
          </span>
          <div className="min-w-0">
            <h2 className="text-lg font-semibold">{profile.name}</h2>
            <p className="mt-0.5 text-sm text-muted">
              目标岗位 {profile.targetRole} · {profile.years} 年经验
            </p>
            <div className="mt-1.5 flex flex-wrap items-center gap-2">
              {profile.phone && <span className="tag bg-line/70 font-mono text-muted">{profile.phone}</span>}
              {profile.wechatBound && <span className="tag bg-green-50 text-success">微信已绑定</span>}
            </div>
          </div>
          {!executed && (
            <button className="btn-secondary ml-auto shrink-0 !px-4 !py-2 text-sm" onClick={startEdit}>
              编辑资料
            </button>
          )}
        </header>

        {editing && (
          <div className="mt-4 border-t border-line pt-4">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <label className="block">
                <span className="mb-1.5 block text-xs text-muted">姓名</span>
                <input
                  className="input"
                  value={form.name}
                  maxLength={20}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-xs text-muted">目标岗位</span>
                <input
                  className="input"
                  value={form.targetRole}
                  maxLength={30}
                  placeholder="如 后端工程师"
                  onChange={(e) => setForm({ ...form, targetRole: e.target.value })}
                />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-xs text-muted">工作年限</span>
                <input
                  className="input"
                  type="number"
                  min={0}
                  max={50}
                  value={form.years}
                  onChange={(e) => setForm({ ...form, years: e.target.value })}
                />
              </label>
            </div>
            <p className="mt-2 text-xs text-muted">头像取姓名首字；手机号与微信绑定需重新验证，二期开放换绑。</p>
            <div className="mt-3 flex gap-2">
              <button className="btn-primary !px-4 !py-2 text-sm" onClick={onSaveProfile} disabled={busy !== null}>
                {busy === "profile" ? "保存中…" : "保存"}
              </button>
              <button className="btn-secondary !px-4 !py-2 text-sm" onClick={() => setEditing(false)}>
                取消
              </button>
            </div>
          </div>
        )}
      </section>

      {/* 三统计卡 */}
      <div className="mb-4 grid grid-cols-1 gap-4 md:grid-cols-3">
        <StatCard value={stats.answered} label="累计答题" tone="brand" />
        <StatCard value={`${stats.streak} 天`} label="连续打卡" tone="success" />
        <StatCard value={`${stats.correctRate}%`} label="平均答对率" />
      </div>

      {/* 数据概览 */}
      <section className="card mb-4 p-5">
        <h2 className="text-base font-semibold">我的数据</h2>
        <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
          {[
            { label: "题集", value: counts.sets, href: "/bank" },
            { label: "收藏题目", value: counts.favorites, href: "/bank" },
            { label: "待复习错题", value: stats.pendingReview, href: "/wrong-book" },
            { label: "已掌握", value: stats.mastered, href: "/wrong-book" },
          ].map((item) => (
            <Link
              key={item.label}
              href={item.href}
              className="rounded-btn border border-line px-3.5 py-3 transition-colors hover:border-brand/40"
            >
              <span className="block text-xl font-semibold text-ink">{item.value}</span>
              <span className="mt-0.5 block text-xs text-muted">{item.label}</span>
            </Link>
          ))}
        </div>
      </section>

      {/* 复习提醒（一期唯一开放项） */}
      <section className="card mb-4 p-5">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-base font-semibold">复习提醒</h2>
          <span className="tag bg-brand-light text-brand">艾宾浩斯到期推送</span>
          <button
            className="ml-auto flex items-center gap-2 text-sm"
            onClick={() => onToggleReminder(!settings.reviewReminderEnabled)}
            disabled={busy !== null || executed}
          >
            <span
              className={`relative h-5 w-9 rounded-full transition-colors ${
                settings.reviewReminderEnabled ? "bg-success" : "bg-line"
              }`}
            >
              <span
                className={`absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
                  settings.reviewReminderEnabled ? "translate-x-4" : "translate-x-0"
                }`}
              />
            </span>
            <span className={settings.reviewReminderEnabled ? "text-ink" : "text-muted"}>
              {settings.reviewReminderEnabled ? "已开启" : "已关闭"}
            </span>
          </button>
        </div>
        <div className="mt-4 flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="mb-1.5 block text-xs text-muted">每日提醒时间</span>
            <input
              className="input w-40 font-mono"
              type="time"
              value={remindTime}
              disabled={!settings.reviewReminderEnabled || executed}
              onChange={(e) => setRemindTime(e.target.value)}
            />
          </label>
          <button
            className="btn-secondary !px-4 !py-2 text-sm"
            onClick={onSaveReminder}
            disabled={!settings.reviewReminderEnabled || busy !== null || executed || remindTime === settings.reviewReminderTime}
          >
            {busy === "reminder" ? "保存中…" : "保存提醒设置"}
          </button>
        </div>
        <p className="mt-3 text-xs leading-relaxed text-muted">
          当前为站内提醒；App / 小程序期接入系统 Push（复用同一份提醒时间偏好）。
          到期队列按艾宾浩斯五档（1/2/4/7/15 天）生成，连对 3 次自动归档。
        </p>
      </section>

      {/* 二期入口占位：只说明不建空壳页 */}
      <section className="card mb-4 p-5">
        <h2 className="text-base font-semibold">更多设置</h2>
        <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2">
          {PHASE2_ITEMS.map((item) => (
            <div
              key={item.label}
              className="flex items-center gap-3 rounded-btn border border-line px-3.5 py-3 opacity-70"
            >
              <div className="min-w-0">
                <p className="text-sm text-ink">{item.label}</p>
                <p className="mt-0.5 text-xs text-muted">{item.desc}</p>
              </div>
              <span className="tag ml-auto shrink-0 bg-line/70 text-muted">二期</span>
            </div>
          ))}
        </div>
      </section>

      {/* 账号操作：退出登录 + 注销（危险操作独立分区） */}
      <section className="card p-5">
        <h2 className="text-base font-semibold">账号</h2>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button className="btn-secondary !px-4 !py-2 text-sm" onClick={onLogout} disabled={busy !== null}>
            {busy === "logout" ? "退出中…" : "退出登录"}
          </button>
          {!executed && (
            <button
              className="ml-auto text-sm text-muted underline-offset-2 transition-colors hover:text-danger hover:underline"
              onClick={() => setConfirmOpen(true)}
              disabled={busy !== null}
            >
              注销账号并删除全部数据
            </button>
          )}
        </div>
        <p className="mt-3 text-xs leading-relaxed text-muted">
          退出登录只清除本机登录态，数据保留；注销会删除全部个人数据，提交后进入 7 天冷静期，期内可撤回。
        </p>
      </section>

      {/* 注销二次确认：明示删除范围 + 手动输入确认词 */}
      {confirmOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 px-4">
          <div className="card w-full max-w-md p-6">
            <h3 className="text-lg font-semibold text-danger">确认注销账号？</h3>
            <p className="mt-2 text-sm leading-relaxed text-muted">
              以下数据将被彻底删除，且冷静期结束后不可恢复：
            </p>
            <ul className="mt-3 flex flex-col gap-1.5">
              {(deactivation?.scopes.length ? deactivation.scopes : DELETION_SCOPES).map((s) => (
                <li key={s} className="flex gap-2 text-xs text-ink/80">
                  <span className="text-danger">•</span>
                  {s}
                </li>
              ))}
            </ul>
            <label className="mt-4 block">
              <span className="mb-1.5 block text-xs text-muted">注销原因（选填，用于改进产品）</span>
              <input
                className="input"
                value={reason}
                maxLength={200}
                placeholder="如：已找到工作 / 不再需要"
                onChange={(e) => setReason(e.target.value)}
              />
            </label>
            <label className="mt-3 block">
              <span className="mb-1.5 block text-xs text-muted">
                请输入「注销」以确认（提交后 7 天内可撤回）
              </span>
              <input
                className="input"
                value={confirmText}
                placeholder="注销"
                onChange={(e) => setConfirmText(e.target.value)}
              />
            </label>
            <div className="mt-5 flex justify-end gap-2">
              <button
                className="btn-secondary !px-4 !py-2 text-sm"
                onClick={() => {
                  setConfirmOpen(false);
                  setConfirmText("");
                }}
              >
                再想想
              </button>
              <button
                className="btn-primary !bg-danger !px-4 !py-2 text-sm"
                onClick={onDeactivate}
                disabled={confirmText.trim() !== "注销" || busy !== null}
              >
                {busy === "deactivate" ? "提交中…" : "确认注销"}
              </button>
            </div>
          </div>
        </div>
      )}

      {toast && (
        <div className="fixed bottom-8 left-1/2 z-50 -translate-x-1/2 rounded-full bg-ink px-5 py-2.5 text-sm text-white shadow-lg">
          {toast}
        </div>
      )}
    </div>
  );
}
