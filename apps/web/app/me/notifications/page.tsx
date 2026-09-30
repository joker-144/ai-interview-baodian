"use client";

/**
 * 通知中心（二期批 4，产品文档 4.2 / 8.3 /me/notifications）
 *
 * - 站内信列表（倒序）、单条/全部已读；
 * - 浏览器通知开关：Notification.requestPermission（偏好存 localStorage）；
 * - Web 期口径：站内信 + 浏览器通知，无推送服务——页面打开期间每 60s 轮询，
 *   未读数增加时弹浏览器通知（页面关闭即停，不做后台推送）；
 * - 顶栏不加导航项（8.3 约束），入口在 /me 内。
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/ui";
import { getNotifications, readAllNotifications, readNotification } from "@/lib/api";
import type { NotificationItem } from "@/lib/types";

const POLL_MS = 60_000;
const LS_BROWSER_NOTIFY = "aib:browserNotify"; // "on" | "off"

const TYPE_META: Record<
  string,
  { label: string; render: (p: NotificationItem["payload"]) => string; href?: (p: NotificationItem["payload"]) => string }
> = {
  generate_done: {
    label: "题库生成完成",
    render: (p) => `已生成 ${p?.generated ?? 0} 题，可去题库中心刷题`,
  },
  exam_report: {
    label: "模考报告已生成",
    render: (p) => `得分 ${p?.score ?? 0} 分，点击查看完整报告`,
    href: (p) => `/exam/report/${p?.examId ?? ""}`,
  },
  review_due: {
    label: "复习提醒",
    render: (p) => `${p?.count ?? 0} 道错题今日到期，连对 3 次可提前毕业`,
    href: () => "/wrong-book",
  },
  interview_prep: {
    label: "面试临近提醒",
    render: (p) =>
      `${p?.jobName ?? "岗位"} 面试定于 ${p?.interviewAt ?? "近期"}，建议先过一遍定向题库`,
    href: (p) => (p?.setId ? `/practice/${p.setId}` : "/pipeline"),
  },
};

export default function NotificationsPage() {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [browserNotify, setBrowserNotify] = useState(false);
  /** 轮询基线：只对页面打开期间新增的未读弹浏览器通知 */
  const knownUnread = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await getNotifications();
      setItems(data.items);
      setUnread(data.unread);
      setError("");
      // 轮询期间发现新未读 → 浏览器通知（首次加载只建立基线，不弹）
      if (
        knownUnread.current !== null &&
        data.unread > knownUnread.current &&
        typeof window !== "undefined" &&
        window.localStorage.getItem(LS_BROWSER_NOTIFY) === "on" &&
        "Notification" in window &&
        Notification.permission === "granted"
      ) {
        const latest = data.items.find((n) => !n.read);
        if (latest) {
          const meta = TYPE_META[latest.type];
          new Notification("AI 面试练习平台", {
            body: meta ? `${meta.label}：${meta.render(latest.payload)}` : "你有新的站内通知",
          });
        }
      }
      knownUnread.current = data.unread;
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载通知失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    setBrowserNotify(
      typeof window !== "undefined" && window.localStorage.getItem(LS_BROWSER_NOTIFY) === "on",
    );
  }, []);

  const toggleBrowserNotify = async () => {
    if (browserNotify) {
      window.localStorage.setItem(LS_BROWSER_NOTIFY, "off");
      setBrowserNotify(false);
      return;
    }
    if (!("Notification" in window)) {
      setError("当前浏览器不支持桌面通知");
      return;
    }
    const permission =
      Notification.permission === "granted"
        ? "granted"
        : await Notification.requestPermission();
    if (permission === "granted") {
      window.localStorage.setItem(LS_BROWSER_NOTIFY, "on");
      setBrowserNotify(true);
      new Notification("AI 面试练习平台", { body: "浏览器通知已开启" });
    } else {
      setError("浏览器通知权限被拒绝，可在浏览器设置中重新开启");
    }
  };

  const handleRead = async (id: string) => {
    setBusyId(id);
    try {
      await readNotification(id);
      setItems((list) => list.map((n) => (n.id === id ? { ...n, read: true } : n)));
      setUnread((u) => Math.max(0, u - 1));
      knownUnread.current = knownUnread.current === null ? null : Math.max(0, knownUnread.current - 1);
      // 顶栏未读角标即时同步
      window.dispatchEvent(new Event("notifications:refresh"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "标记已读失败");
    } finally {
      setBusyId(null);
    }
  };

  const handleReadAll = async () => {
    setBusyId("*");
    try {
      await readAllNotifications();
      setItems((list) => list.map((n) => ({ ...n, read: true })));
      setUnread(0);
      knownUnread.current = 0;
      // 顶栏未读角标即时同步
      window.dispatchEvent(new Event("notifications:refresh"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "全部已读失败");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div>
      <PageHeader title="通知中心" sub={loading ? "加载中…" : `站内信 ${items.length} 条 · 未读 ${unread} 条`} />

      <div className="card p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-base font-semibold">站内通知</h2>
          <div className="flex items-center gap-3">
            {/* 浏览器通知开关（Web 期：站内信 + 浏览器通知，无推送服务） */}
            <label className="flex cursor-pointer items-center gap-2 text-xs text-muted">
              <span>浏览器通知</span>
              <button
                role="switch"
                aria-checked={browserNotify}
                onClick={toggleBrowserNotify}
                className={`relative h-5 w-9 rounded-full transition-colors ${
                  browserNotify ? "bg-success" : "bg-line"
                }`}
              >
                <span
                  className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${
                    browserNotify ? "translate-x-4" : "translate-x-0"
                  }`}
                />
              </button>
            </label>
            <button
              className="btn-secondary !px-3 !py-1.5 text-xs"
              onClick={handleReadAll}
              disabled={busyId !== null || unread === 0}
            >
              {busyId === "*" ? "处理中…" : "全部已读"}
            </button>
          </div>
        </div>

        {error && (
          <p className="mb-3 rounded-btn bg-red-50 px-3 py-2 text-xs text-danger">{error}</p>
        )}

        {loading ? (
          <div className="flex h-32 items-center justify-center text-sm text-muted">加载中…</div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
            <p className="text-sm text-ink">暂无通知</p>
            <p className="text-xs text-muted">出题完成、模考报告生成、复习到期时会在这里提醒你</p>
          </div>
        ) : (
          <ul className="space-y-3">
            {items.map((n) => {
              const meta = TYPE_META[n.type];
              const href = meta?.href?.(n.payload);
              const inner = (
                <div
                  className={`flex items-start gap-3 rounded-btn border px-4 py-3 transition-colors ${
                    n.read ? "border-line opacity-75" : "border-brand/40 bg-brand-light/30"
                  } ${href ? "cursor-pointer hover:border-brand/60" : ""}`}
                >
                  <span
                    className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
                      n.read ? "bg-line" : "bg-brand"
                    }`}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="flex items-center gap-2 text-sm font-medium text-ink">
                      {meta?.label ?? "站内通知"}
                      {!n.read && (
                        <span className="tag shrink-0 bg-brand/10 text-brand">未读</span>
                      )}
                    </p>
                    <p className="mt-0.5 text-xs leading-relaxed text-muted">
                      {meta ? meta.render(n.payload) : "点击查看详情"}
                    </p>
                    <p className="mt-1 text-xs text-muted">{n.createdAt.slice(0, 16)}</p>
                  </div>
                  {!n.read && (
                    <button
                      className="shrink-0 self-center text-xs text-muted underline-offset-2 transition-colors hover:text-brand hover:underline"
                      onClick={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        handleRead(n.id);
                      }}
                      disabled={busyId === n.id}
                    >
                      {busyId === n.id ? "处理中…" : "标为已读"}
                    </button>
                  )}
                </div>
              );
              return (
                <li key={n.id}>
                  {href && !n.read ? (
                    <Link
                      href={href}
                      className="block"
                      onClick={() => handleRead(n.id)}
                    >
                      {inner}
                    </Link>
                  ) : (
                    inner
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <p className="mt-3 text-xs leading-relaxed text-muted">
        Web 期口径：站内信 + 浏览器通知（页面打开期间每 60 秒轮询），App / 小程序期接入系统 Push。
      </p>
    </div>
  );
}
