"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { loginByPhone, loginByWechat } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const [loading, setLoading] = useState(false);

  const sendCode = () => {
    if (!/^1\d{10}$/.test(phone) || countdown > 0) return;
    setCodeSent(true);
    setCountdown(60);
    const timer = setInterval(() => {
      setCountdown((c) => {
        if (c <= 1) clearInterval(timer);
        return c - 1;
      });
    }, 1000);
  };

  const doLogin = async () => {
    if (!/^1\d{10}$/.test(phone) || code.length < 4) return;
    setLoading(true);
    await loginByPhone(phone, code);
    router.push("/");
  };

  const doWechat = async () => {
    setLoading(true);
    await loginByWechat();
    router.push("/");
  };

  return (
    <div className="flex min-h-[calc(100vh-3.5rem)] items-center justify-center">
      <div className="card w-full max-w-sm p-8">
        <div className="mb-6 flex flex-col items-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-brand text-white">
            <svg width="22" height="22" viewBox="0 0 16 16" fill="none">
              <path d="M3 8.5L6.5 12L13 4.5" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
          <h1 className="mt-4 text-xl font-semibold">登录面试宝典</h1>
          <p className="mt-1 text-sm text-muted">系统化刷题，高效拿下心仪 Offer</p>
        </div>

        <label className="mb-2 block text-sm text-ink">手机号</label>
        <input
          className="input"
          placeholder="请输入手机号"
          value={phone}
          maxLength={11}
          onChange={(e) => setPhone(e.target.value.replace(/\D/g, ""))}
        />

        <label className="mb-2 mt-4 block text-sm text-ink">验证码</label>
        <div className="flex gap-2">
          <input
            className="input"
            placeholder="输入任意 4 位验证码（Mock）"
            value={code}
            maxLength={6}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
          />
          <button
            className="btn-secondary shrink-0 whitespace-nowrap"
            disabled={!/^1\d{10}$/.test(phone) || countdown > 0}
            onClick={sendCode}
          >
            {countdown > 0 ? `${countdown}s` : codeSent ? "重新发送" : "获取验证码"}
          </button>
        </div>

        <button
          className="btn-primary mt-6 w-full"
          disabled={loading || !/^1\d{10}$/.test(phone) || code.length < 4}
          onClick={doLogin}
        >
          登录 / 注册
        </button>

        <div className="my-5 flex items-center gap-3 text-xs text-muted">
          <span className="h-px flex-1 bg-line" />
          其他方式
          <span className="h-px flex-1 bg-line" />
        </div>

        <button className="btn-secondary w-full" onClick={doWechat} disabled={loading}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="#1E9E6A">
            <path d="M8.7 4C4.9 4 2 6.6 2 9.9c0 1.8 1 3.5 2.5 4.6l-.6 1.9 2.2-1.1c.8.2 1.5.3 2.3.3h.6c-.2-.6-.3-1.2-.3-1.8 0-3.2 3-5.8 6.8-5.8h.4C15.3 5.7 12.3 4 8.7 4zm-2 3a.9.9 0 110 1.8.9.9 0 010-1.8zm4.5 0a.9.9 0 110 1.8.9.9 0 010-1.8zM15.6 9c-3.3 0-6 2.2-6 4.9s2.7 4.9 6 4.9c.7 0 1.3-.1 1.9-.3l1.9 1-.5-1.7c1.6-.9 2.6-2.4 2.6-4 0-2.8-2.7-4.8-5.9-4.8zm-2 2.4a.8.8 0 110 1.5.8.8 0 010-1.5zm4 0a.8.8 0 110 1.5.8.8 0 010-1.5z" />
          </svg>
          微信一键登录
        </button>

        <p className="mt-5 text-center text-xs text-muted">
          登录即代表同意《用户协议》与《隐私政策》· 本产品面向 16+ 求职用户
        </p>
      </div>
    </div>
  );
}
