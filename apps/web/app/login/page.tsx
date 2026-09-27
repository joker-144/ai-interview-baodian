"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { loginByAccount, registerAccount } from "@/lib/api";

type Mode = "login" | "register";

/** 与后端 RegisterRequest 校验对齐：账号 2~64，密码 6~64 */
const ACCOUNT_RE = /^[A-Za-z0-9_@\-\u4e00-\u9fa5]{2,64}$/;

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("login");
  const [account, setAccount] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const accountValid = ACCOUNT_RE.test(account);
  const passwordValid = password.length >= 6 && password.length <= 64;
  const canSubmit = accountValid && passwordValid && (mode === "login" || name.length <= 32);

  const submit = async () => {
    if (!canSubmit || loading) return;
    setLoading(true);
    setError("");
    try {
      if (mode === "login") {
        await loginByAccount(account.trim(), password);
      } else {
        await registerAccount(account.trim(), password, name.trim() || undefined);
      }
      router.push("/");
    } catch (e) {
      setError(e instanceof Error ? e.message : "登录失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  };

  const fillDemo = () => {
    setMode("login");
    setAccount("demo");
    setPassword("demo1234");
    setError("");
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

        {/* 登录 / 注册 切换 */}
        <div className="mb-5 grid grid-cols-2 gap-1 rounded-btn bg-bg p-1 text-sm font-medium">
          {(["login", "register"] as Mode[]).map((m) => (
            <button
              key={m}
              className={`rounded-[7px] py-2 transition-colors ${
                mode === m ? "bg-white text-ink shadow-sm" : "text-muted hover:text-ink"
              }`}
              onClick={() => {
                setMode(m);
                setError("");
              }}
            >
              {m === "login" ? "账号登录" : "注册新账号"}
            </button>
          ))}
        </div>

        <label className="mb-2 block text-sm text-ink">账号</label>
        <input
          className="input"
          placeholder="支持字母 / 数字 / 中文 / _ @ -"
          value={account}
          maxLength={64}
          onChange={(e) => setAccount(e.target.value)}
        />

        <label className="mb-2 mt-4 block text-sm text-ink">密码</label>
        <input
          className="input"
          type="password"
          placeholder="6~64 位密码"
          value={password}
          maxLength={64}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />

        {mode === "register" && (
          <>
            <label className="mb-2 mt-4 block text-sm text-ink">昵称（可选）</label>
            <input
              className="input"
              placeholder="展示用昵称，默认取账号"
              value={name}
              maxLength={32}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
            />
            <p className="mt-2 text-xs text-muted">注册成功后自动登录</p>
          </>
        )}

        {error && (
          <p className="mt-4 rounded-btn bg-red-50 px-3 py-2 text-sm text-red-600">{error}</p>
        )}

        <button className="btn-primary mt-6 w-full" disabled={!canSubmit || loading} onClick={submit}>
          {loading ? "请稍候…" : mode === "login" ? "登录" : "注册并登录"}
        </button>

        <button
          className="mt-3 w-full text-center text-xs text-muted hover:text-brand"
          onClick={fillDemo}
        >
          体验演示账号：demo / demo1234（点击填充）
        </button>

        <div className="my-5 flex items-center gap-3 text-xs text-muted">
          <span className="h-px flex-1 bg-line" />
          其他方式
          <span className="h-px flex-1 bg-line" />
        </div>

        {/* 短信 / 微信登录：接口已预留（后端 501），一期未开通 */}
        <div className="grid grid-cols-2 gap-2">
          <button className="btn-secondary w-full opacity-50" disabled title="暂未开通">
            手机验证码登录（敬请期待）
          </button>
          <button className="btn-secondary w-full opacity-50" disabled title="暂未开通">
            微信一键登录（敬请期待）
          </button>
        </div>

        <p className="mt-5 text-center text-xs text-muted">
          登录即代表同意《用户协议》与《隐私政策》· 本产品面向 16+ 求职用户
        </p>
      </div>
    </div>
  );
}
