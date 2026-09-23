"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getUser, isLoggedIn, logout } from "@/lib/api";
import type { UserProfile } from "@/lib/types";

const NAV_ITEMS = [
  { href: "/", label: "首页" },
  { href: "/bank", label: "题库" },
  { href: "/resume", label: "简历分析" },
  { href: "/jobs", label: "岗位检索" },
  { href: "/jd", label: "JD定制" },
  { href: "/wrong-book", label: "错题本" },
  { href: "/interview", label: "模拟面试" },
  { href: "/pipeline", label: "求职看板" },
];

export function TopNav() {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<UserProfile | null>(null);
  const [loggedIn, setLoggedIn] = useState(false);

  useEffect(() => {
    setLoggedIn(isLoggedIn());
    getUser().then(setUser);
  }, [pathname]);

  // 登录页不渲染顶栏
  if (pathname === "/login") return null;

  const active = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-card/95 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-page items-center gap-6 px-6">
        {/* Logo：蓝色圆角方块 + 对勾 */}
        <Link href="/" className="flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand text-white">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path
                d="M3 8.5L6.5 12L13 4.5"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
          <span className="text-base font-semibold tracking-wide">面试宝典</span>
        </Link>

        {/* 导航链接 */}
        <nav className="flex flex-1 items-center gap-1 overflow-x-auto">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={`whitespace-nowrap rounded-lg px-3 py-1.5 text-sm transition-colors ${
                active(item.href)
                  ? "font-medium text-brand"
                  : "text-muted hover:text-ink"
              }`}
            >
              {item.label}
            </Link>
          ))}
        </nav>

        {/* 右侧操作区 */}
        <div className="flex items-center gap-4">
          {user && (
            <span className="flex items-center gap-1 text-sm font-medium text-warn" title="连续学习">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                <path d="M13.5 0.7c0.6 3.9-1.6 6.1-3.4 8-1.6 1.7-3.1 3.3-3.1 6 0 4 3.1 7.3 7 7.3s7-3.3 7-7.3c0-2.6-1.2-4.7-2.6-6.5-0.4 1.5-1.2 2.6-2.4 3.4 0.3-3.7-1.2-8-2.5-10.9z" />
              </svg>
              连续 {user.streak} 天
            </span>
          )}
          {/* 通知铃铛 */}
          <button className="relative text-muted transition-colors hover:text-ink" title="通知">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M13.7 21a2 2 0 01-3.4 0" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-danger" />
          </button>
          {/* 头像 / 登录 */}
          {loggedIn && user ? (
            <button
              onClick={async () => {
                await logout();
                router.push("/login");
              }}
              title="点击退出登录"
              className="flex h-8 w-8 items-center justify-center rounded-full bg-ink text-sm text-white"
            >
              {user.avatarText}
            </button>
          ) : (
            <Link href="/login" className="btn-primary !px-4 !py-1.5 text-sm">
              登录
            </Link>
          )}
        </div>
      </div>
    </header>
  );
}
