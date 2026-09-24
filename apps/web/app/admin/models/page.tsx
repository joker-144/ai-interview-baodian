"use client";

/**
 * 管理端 · 模型配置（产品文档 4.6.1，页面清单 A1）
 *
 * 定位：仅平台管理员可见的内部工具，不进入 C 端导航，直接访问 /admin/models。
 * 能力：五层分层模型的供应商 / 模型名 / API Key / base_url / 分层参数配置，
 *      连通性自测、保存即热生效、版本回滚、变更审计。
 * 安全：API Key 只回显掩码（sk-****abcd），保存与日志链路均不出现明文。
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  adminLogin,
  adminLogout,
  discoverModels,
  getAuditLog,
  getLlmConfigs,
  getLlmHistoryCount,
  getLocalModels,
  getProviders,
  isAdminLoggedIn,
  rollbackLlmConfig,
  testLlmConfig,
  updateLlmConfig,
} from "@/lib/api";
import {
  AUDIT_ACTION_LABEL,
  LLM_PARAM_META,
  type AuditEntry,
  type LlmConfig,
  type LlmLayer,
  type LlmProvider,
  type LlmTestResult,
  type LocalModelList,
  type ModelOption,
} from "@/lib/types";

/** 单层表单草稿：params 以字符串承载输入，保存时按原类型回转 */
interface Draft {
  provider: string;
  modelName: string;
  apiKey: string; // 空 = 不修改；回显掩码同样视为不修改
  baseUrl: string;
  fallbackModel: string;
  enabled: boolean;
  params: Record<string, string>;
}

type Busy = { layer: LlmLayer; action: "save" | "test" | "rollback" | "discover" } | null;

/** 模型清单来源标识（与后端 discover 接口的 source 对齐） */
const SOURCE_LABEL: Record<string, string> = {
  remote: "已从供应商实时拉取",
  local: "项目内置本地模型",
  static: "拉取失败，以下为常用候选",
  none: "未获取到候选",
};

/** API Key / base_url 输入停顿后自动拉取模型的防抖时长 */
const DISCOVER_DEBOUNCE_MS = 700;

/** 变更审计表每页条数：日志只增不减，分页避免区块无限拉长 */
const AUDIT_PAGE_SIZE = 10;

/** 发现请求指纹：同一组（供应商, base_url, Key）只拉一次，避免逐字符重复请求 */
const discoverSig = (providerId: string, baseUrl: string, apiKey: string) =>
  `${providerId}|${baseUrl.trim()}|${apiKey.trim()}`;

function toDraft(cfg: LlmConfig): Draft {
  return {
    provider: cfg.provider,
    modelName: cfg.modelName,
    apiKey: "",
    baseUrl: cfg.baseUrl,
    fallbackModel: cfg.fallbackModel,
    enabled: cfg.enabled,
    params: Object.fromEntries(Object.entries(cfg.params).map(([k, v]) => [k, String(v)])),
  };
}

/** 参数值按种子类型回转（数字字段非法输入直接抛错，避免脏数据落库） */
function parseParams(cfg: LlmConfig, draft: Draft): Record<string, number | string> {
  const out: Record<string, number | string> = {};
  Object.entries(draft.params).forEach(([key, raw]) => {
    const text = raw.trim();
    if (typeof cfg.params[key] === "number") {
      const num = Number(text);
      if (!text || Number.isNaN(num)) throw new Error(`参数「${key}」需为数字`);
      out[key] = num;
    } else {
      out[key] = text;
    }
  });
  return out;
}

export default function AdminModelsPage() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [token, setToken] = useState("");
  const [tokenError, setTokenError] = useState<string | null>(null);
  const [loggingIn, setLoggingIn] = useState(false);

  const [configs, setConfigs] = useState<LlmConfig[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [historyCount, setHistoryCount] = useState<Record<string, number>>({});
  const [testResults, setTestResults] = useState<Record<string, LlmTestResult | null>>({});
  const [notices, setNotices] = useState<Record<string, string | null>>({});
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  /** 审计表当前页（新日志到达时在 effect 中收敛越界） */
  const [auditPage, setAuditPage] = useState(1);
  const [busy, setBusy] = useState<Busy>(null);
  const [toast, setToast] = useState<string | null>(null);
  // 供应商与模型发现：选定供应商 → 自动回填 base_url → 拉取可用模型（也允许手填）
  const [providers, setProviders] = useState<LlmProvider[]>([]);
  const [localModels, setLocalModels] = useState<LocalModelList | null>(null);
  const [modelOptions, setModelOptions] = useState<Record<string, ModelOption[]>>({});
  const [discoverNote, setDiscoverNote] = useState<Record<string, string | null>>({});
  /** 当前展开模型下拉面板的分层（datalist 在部分浏览器点不开，改为自控面板） */
  const [openMenu, setOpenMenu] = useState<LlmLayer | null>(null);

  const notify = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 2600);
  };

  const loadAudit = useCallback(async () => {
    setAudit(await getAuditLog());
  }, []);

  // 日志刷新后页码越界时收敛到末页（如停在末页时旧记录被顶出 200 条上限）
  const auditTotalPages = Math.max(1, Math.ceil(audit.length / AUDIT_PAGE_SIZE));
  useEffect(() => {
    setAuditPage((p) => Math.min(p, auditTotalPages));
  }, [auditTotalPages]);

  /** 供应商注册表 + 本地模型清单（一次拉取，全页面共用） */
  const loadCatalog = useCallback(async () => {
    const [list, locals] = await Promise.all([getProviders(), getLocalModels()]);
    setProviders(list);
    setLocalModels(locals);
    // 本地模型无需 Key 即可枚举，先把 Embedding 层的候选填上
    setModelOptions((prev) => ({
      ...prev,
      embedding:
        prev.embedding?.length
        ? prev.embedding
        : locals.models.map((m) => ({
            id: m.modelId,
            label: `${m.label}（dim ${m.dimensions ?? "?"}）`,
            kind: m.kind,
          })),
    }));
  }, []);

  const loadConfigs = useCallback(async () => {
    const list = await getLlmConfigs();
    setConfigs(list);
    setDrafts((prev) => {
      const next: Record<string, Draft> = {};
      list.forEach((cfg) => {
        // 保留用户正在编辑的草稿（如 API Key 输入），其余按服务端值刷新
        const old = prev[cfg.layer];
        next[cfg.layer] = old ? { ...toDraft(cfg), apiKey: old.apiKey } : toDraft(cfg);
      });
      return next;
    });
    const counts = await Promise.all(
      list.map(async (cfg) => [cfg.layer, await getLlmHistoryCount(cfg.layer)] as const),
    );
    setHistoryCount(Object.fromEntries(counts));
  }, []);

  useEffect(() => {
    const ok = isAdminLoggedIn();
    setAuthed(ok);
    if (ok) {
      loadConfigs();
      loadAudit();
      loadCatalog();
    }
  }, [loadConfigs, loadAudit, loadCatalog]);

  const onLogin = async () => {
    if (!token.trim()) {
      setTokenError("请输入管理口令");
      return;
    }
    setLoggingIn(true);
    const ok = await adminLogin(token);
    setLoggingIn(false);
    if (!ok) {
      setTokenError("口令不正确，请重试");
      return;
    }
    setTokenError(null);
    setAuthed(true);
    await loadConfigs();
    await loadAudit();
    await loadCatalog();
  };

  const onLogout = async () => {
    await adminLogout();
    setAuthed(false);
    setToken("");
    setConfigs([]);
    setDrafts({});
  };

  const patchDraft = (layer: string, patch: Partial<Draft>) =>
    setDrafts((prev) => ({ ...prev, [layer]: { ...prev[layer], ...patch } }));

  const run = async (
    layer: LlmLayer,
    action: NonNullable<Busy>["action"],
    fn: () => Promise<void>,
  ) => {
    setBusy({ layer, action });
    try {
      await fn();
    } catch (e) {
      notify(e instanceof Error ? e.message : "操作失败，请重试");
    } finally {
      setBusy(null);
    }
  };

  const onSave = (cfg: LlmConfig) =>
    run(cfg.layer, "save", async () => {
      const draft = drafts[cfg.layer];
      let params: Record<string, number | string>;
      try {
        params = parseParams(cfg, draft);
      } catch (e) {
        notify(e instanceof Error ? e.message : "参数格式有误");
        return;
      }
      // 回传掩码 = 未修改（接口侧同样兜底，前端先过滤，避免明文误传）
      const apiKey = draft.apiKey.trim();
      const updated = await updateLlmConfig(cfg.layer, {
        provider: draft.provider,
        modelName: draft.modelName.trim(),
        baseUrl: draft.baseUrl.trim(),
        fallbackModel: draft.fallbackModel.trim(),
        enabled: draft.enabled,
        params,
        ...(apiKey && !apiKey.includes("****") ? { apiKey } : {}),
      });
      setConfigs((prev) => prev.map((c) => (c.layer === cfg.layer ? updated : c)));
      setDrafts((prev) => ({ ...prev, [cfg.layer]: { ...toDraft(updated), apiKey: "" } }));
      setNotices((prev) => ({ ...prev, [cfg.layer]: updated.notice ?? null }));
      setTestResults((prev) => ({ ...prev, [cfg.layer]: null }));
      setHistoryCount((prev) => ({ ...prev, [cfg.layer]: (prev[cfg.layer] || 0) + 1 }));
      await loadAudit();
      notify(updated.notice ? `已保存：${updated.notice}` : "已保存并热生效，新任务立即使用该配置");
    });

  const onTest = (cfg: LlmConfig) =>
    run(cfg.layer, "test", async () => {
      const result = await testLlmConfig(cfg.layer);
      setTestResults((prev) => ({ ...prev, [cfg.layer]: result }));
      await loadAudit();
      notify(
        result.ok
          ? `连通正常：${result.detail || `延迟 ${result.latencyMs}ms${result.tokens ? ` / ${result.tokens} tokens` : ""}`}`
          : `连通失败：${result.error}`,
      );
    });

  const onRollback = (cfg: LlmConfig) =>
    run(cfg.layer, "rollback", async () => {
      const restored = await rollbackLlmConfig(cfg.layer);
      setConfigs((prev) => prev.map((c) => (c.layer === cfg.layer ? restored : c)));
      setDrafts((prev) => ({ ...prev, [cfg.layer]: toDraft(restored) }));
      setNotices((prev) => ({ ...prev, [cfg.layer]: restored.notice ?? null }));
      setTestResults((prev) => ({ ...prev, [cfg.layer]: null }));
      setHistoryCount((prev) => ({ ...prev, [cfg.layer]: Math.max(0, (prev[cfg.layer] || 1) - 1) }));
      await loadAudit();
      notify("已回滚到上一版配置");
    });

  const providerOf = (id: string) => providers.find((p) => p.id === id);

  /** 已拉取过的（供应商, base_url, Key）指纹，用于跳过重复发现请求 */
  const lastDiscoverSig = useRef<Record<string, string>>({});

  /**
   * 拉取可用模型：填好供应商 + Key 后自动返回模型清单；
   * 拉不到（未填 Key / 网络不通 / 接口漂移）则回落常用候选，且始终允许手动输入。
   * override 用于“刚切供应商、草稿还未入 state”的竞态场景。
   */
  const onDiscover = (
    cfg: LlmConfig,
    override?: { provider?: string; baseUrl?: string; apiKey?: string },
  ) =>
    run(cfg.layer, "discover", async () => {
      const draft = drafts[cfg.layer];
      const provider = override?.provider ?? draft.provider;
      const baseUrl = override?.baseUrl ?? draft.baseUrl.trim();
      const apiKey = override?.apiKey ?? draft.apiKey.trim();
      // 记下本次指纹：手动重试与自动拉取共用，避免同一输入反复请求
      lastDiscoverSig.current[cfg.layer] = discoverSig(provider, baseUrl, apiKey);
      const result = await discoverModels({ provider, baseUrl, apiKey, layer: cfg.layer });
      setModelOptions((prev) => ({ ...prev, [cfg.layer]: result.models }));
      // 发现返回候选后，若该层尚未选中模型（刚切供应商被清空），自动选中第一个候选；
      // 已有模型名（含手填的自定义名）不覆盖
      if (result.models.length) {
        setDrafts((prev) => {
          const d = prev[cfg.layer];
          if (!d || d.modelName.trim()) return prev;
          return { ...prev, [cfg.layer]: { ...d, modelName: result.models[0].id } };
        });
      }
      setDiscoverNote((prev) => ({
        ...prev,
        [cfg.layer]: `${SOURCE_LABEL[result.source] ?? result.source}·共 ${result.models.length} 个${
          result.error ? `（${result.error}）` : ""
        }`,
      }));
      await loadAudit();
      notify(
        result.ok
          ? `已获取 ${result.models.length} 个可用模型，也可直接手动输入`
          : result.error || "未获取到模型清单，可手动输入模型名",
      );
    });

  /** 切换供应商：base_url 随之自动切换；本地供应商直接选中已下载模型 */
  const onProviderChange = async (cfg: LlmConfig, providerId: string) => {
    const provider = providerOf(providerId);
    if (!provider) return;
    const isLocal = provider.kind === "local";
    const locals = localModels?.models ?? [];
    patchDraft(cfg.layer, {
      provider: providerId,
      // 本地模型无 base_url；其余供应商回填各自默认地址（仍可手改为自建网关）
      baseUrl: isLocal ? "" : provider.baseUrl,
      // 切供应商后原模型名 / 备用模型属于旧供应商（继续用会 404）：
      // 本地直接选中第一个已下载模型；云端先清空，待发现成功后自动选中第一个候选（见 onDiscover）
      ...(isLocal && locals.length
        ? { modelName: locals[0].modelId, fallbackModel: "" }
        : { modelName: "", fallbackModel: "" }),
    });
    setTestResults((prev) => ({ ...prev, [cfg.layer]: null }));
    if (isLocal) {
      // 本地清单已在手上，无需走发现接口
      setModelOptions((prev) => ({
        ...prev,
        [cfg.layer]: locals.map((m) => ({
          id: m.modelId,
          label: `${m.label}（dim ${m.dimensions ?? "?"}）`,
          kind: m.kind,
        })),
      }));
      setDiscoverNote((prev) => ({
        ...prev,
        [cfg.layer]: locals.length
          ? `${SOURCE_LABEL.local}·共 ${locals.length} 个`
          : "本地模型尚未下载，请先执行下载脚本",
      }));
      return;
    }
    setModelOptions((prev) => ({ ...prev, [cfg.layer]: [] }));
    await onDiscover(cfg, { provider: providerId, baseUrl: provider.baseUrl });
  };

  // onDiscover 每次渲染重建，用 ref 持有最新闭包（内部要读最新 drafts），避免 effect 依赖抖动
  const discoverRef = useRef(onDiscover);
  discoverRef.current = onDiscover;

  /**
   * API Key 输入停顿后自动拉取该账号可用模型（无需手动点按钮）。
   * 不触发的情况：本地 / 无需 Key 的供应商、Key 为空、回显掩码（掩码 = 未修改）、指纹未变。
   * effect 每次 drafts 变化先清掉旧定时器再重设，即为防抖。
   */
  useEffect(() => {
    const timers: number[] = [];
    configs.forEach((cfg) => {
      const draft = drafts[cfg.layer];
      if (!draft) return;
      const provider = providers.find((p) => p.id === draft.provider);
      if (provider?.kind === "local" || provider?.requiresKey === false) return;
      const key = draft.apiKey.trim();
      if (!key || key.includes("****")) return;
      const sig = discoverSig(draft.provider, draft.baseUrl, key);
      if (lastDiscoverSig.current[cfg.layer] === sig) return;
      timers.push(
        window.setTimeout(() => discoverRef.current(cfg, { apiKey: key }), DISCOVER_DEBOUNCE_MS),
      );
    });
    return () => timers.forEach((t) => window.clearTimeout(t));
  }, [drafts, configs, providers]);

  /* ---------------- 口令 gate ---------------- */

  if (authed === null) {
    return <div className="py-28 text-center text-sm text-muted">正在校验管理身份…</div>;
  }

  if (!authed) {
    return (
      <div className="mx-auto flex max-w-sm flex-col justify-center py-24">
        <div className="card p-7">
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-ink text-white">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9">
                <rect x="4" y="10.5" width="16" height="10" rx="2.2" />
                <path d="M8 10.5V7.8a4 4 0 018 0v2.7" strokeLinecap="round" />
              </svg>
            </span>
            <h1 className="text-lg font-semibold">管理端 · 模型配置</h1>
          </div>
          <p className="mt-2 text-sm text-muted">
            该页面仅平台管理员可见，用于配置分层模型与 API Key，独立于用户账号体系。
          </p>
          <input
            className="input mt-5"
            type="password"
            value={token}
            placeholder="请输入管理口令"
            onChange={(e) => {
              setToken(e.target.value);
              setTokenError(null);
            }}
            onKeyDown={(e) => e.key === "Enter" && onLogin()}
          />
          {tokenError && <p className="mt-2 text-xs text-danger">{tokenError}</p>}
          <button className="btn-primary mt-4 w-full" onClick={onLogin} disabled={loggingIn}>
            {loggingIn ? "校验中…" : "进入配置"}
          </button>
          <p className="mt-4 text-xs leading-relaxed text-muted">
            开发态口令默认 <span className="font-mono text-ink">admin-dev-token</span>
            （对应后端 ADMIN_TOKEN 环境变量），生产由部署注入并定期轮换。
          </p>
          <Link href="/" className="mt-4 block text-center text-xs text-muted hover:text-brand">
            返回主站
          </Link>
        </div>
      </div>
    );
  }

  /* ---------------- 配置主体 ---------------- */

  return (
    <div>
      {/* 管理端独立顶栏（不带 C 端导航） */}
      <div className="mb-5 mt-2 flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-wide">模型配置</h1>
            <span className="tag bg-ink/10 text-ink">管理端</span>
          </div>
          <p className="mt-1 text-sm text-muted">
            按任务类型分层用模：主模型出题、轻量模型校验、多模态解析图片、Embedding 去重、语音面试。
            保存即热生效，无需重启服务。
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Link href="/" className="btn-secondary !px-4 !py-2 text-sm">
            返回主站
          </Link>
          <button className="btn-secondary !px-4 !py-2 text-sm" onClick={onLogout}>
            退出管理
          </button>
        </div>
      </div>

      <div className="card mb-5 flex items-start gap-2 border-line bg-brand-light/40 px-4 py-3">
        <svg
          className="mt-0.5 shrink-0"
          width="15"
          height="15"
          viewBox="0 0 24 24"
          fill="none"
          stroke="#014DB2"
          strokeWidth="1.8"
        >
          <circle cx="12" cy="12" r="9.2" />
          <path d="M12 11v5.5M12 7.8v.6" strokeLinecap="round" />
        </svg>
        <p className="text-xs leading-relaxed text-ink/80">
          API Key 保存后仅以掩码回显（如 <span className="font-mono">sk-****abcd</span>
          ），接口、页面与审计日志均不出现明文；留空或保持掩码即视为不修改。所有变更留快照可回滚，并记入审计日志。
        </p>
      </div>

      {/* 本地模型：已下载到项目内的权重，离线推理、零 API 成本 */}
      <section className="card mb-5 p-5">
        <header className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h2 className="text-base font-semibold">本地模型</h2>
          <span className="tag bg-brand-light text-brand">离线推理 · 零 API 成本</span>
          <span className="ml-auto font-mono text-xs text-muted">{localModels?.modelsDir}</span>
        </header>
        <p className="mt-1.5 text-xs leading-relaxed text-muted">
          可本地运行的模型（当前为向量模型）下载到项目内后由服务端直接加载调用，不出网、不计费；
          Embedding 层默认已指向本地模型。权重不入仓，克隆项目后执行一次下载命令即可。
        </p>
        {!localModels?.models.length ? (
          <div className="mt-3 rounded-btn bg-orange-50 px-3.5 py-2.5 text-xs text-warn">
            尚未下载本地模型，请在项目根目录执行：
            <span className="font-mono">{localModels?.downloadCommand || "python scripts/download_models.py"}</span>
          </div>
        ) : (
          <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2">
            {localModels.models.map((m) => (
              <div
                key={m.modelId}
                className="flex items-start gap-2.5 rounded-btn border border-line px-3 py-2.5"
              >
                <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-tag bg-success/10 text-success">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
                    <path d="M5 12.5l4.2 4.2L19 7" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </span>
                <div className="min-w-0">
                  <p className="font-mono text-xs text-ink">{m.modelId}</p>
                  <p className="mt-0.5 text-xs text-muted">
                    {m.label} · {m.kind} · dim {m.dimensions ?? "?"} · {m.sizeMB}MB
                    {m.downloadedAt ? ` · ${m.downloadedAt}` : ""}
                  </p>
                  <p className="mt-0.5 text-xs leading-relaxed text-muted/80">{m.desc}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* 五层配置卡 */}
      <div className="flex flex-col gap-4">
        {configs.map((cfg) => {
          const draft = drafts[cfg.layer];
          if (!draft) return null;
          const layerBusy = busy?.layer === cfg.layer ? busy.action : null;
          const test = testResults[cfg.layer] ?? null;
          const notice = notices[cfg.layer] ?? null;
          const canRollback = (historyCount[cfg.layer] || 0) > 0;
          const provider = providerOf(draft.provider);
          const isLocal = provider?.kind === "local";
          const noKeyNeeded = isLocal || provider?.requiresKey === false;
          const layerUnsupported = provider ? !provider.capabilities.includes(cfg.layer) : false;
          const options = modelOptions[cfg.layer] ?? [];
          // 已输入完整候选之一时不再过滤，否则面板会把当前选中项自己筛掉
          const keyword = draft.modelName.trim().toLowerCase();
          const filtered =
            !keyword || options.some((m) => m.id.toLowerCase() === keyword)
              ? options
              : options.filter((m) => `${m.id} ${m.label ?? ""}`.toLowerCase().includes(keyword));
          // 旧配置里可能存在注册表以外的供应商 id，补一个占位项避免 select 显示为空
          const providerOptions: LlmProvider[] = providers.some((p) => p.id === draft.provider)
            ? providers
            : [
                ...providers,
                {
                  id: draft.provider,
                  label: `${draft.provider}（未登记）`,
                  kind: "custom",
                  baseUrl: draft.baseUrl,
                  requiresKey: true,
                  keyPlaceholder: "",
                  capabilities: [],
                  hint: "该供应商不在注册表中，请改选已登记项或自定义（OpenAI 兼容）",
                },
              ];

          return (
            <section key={cfg.layer} className="card p-5">
              {/* 卡头：分层名 + 用途 + 状态 */}
              <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <h2 className="text-base font-semibold">{cfg.label}</h2>
                <span className="tag bg-line/70 font-mono text-xs text-muted">{cfg.layer}</span>
                <span className="text-xs text-muted">{cfg.usage}</span>
                <span className="ml-auto flex items-center gap-2">
                  {cfg.enabled ? (
                    <span className="tag bg-green-50 text-success">已启用</span>
                  ) : (
                    <span className="tag bg-orange-50 text-warn">已停用</span>
                  )}
                  {cfg.hasKey ? (
                    <span className="tag bg-brand-light font-mono text-brand">{cfg.apiKeyMasked}</span>
                  ) : (
                    <span className="tag bg-orange-50 text-warn">未配置 Key</span>
                  )}
                </span>
              </header>

              {/* 基础字段：供应商 → base_url 联动；模型名支持自动拉取 + 手动输入 */}
              <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
                <label className="block">
                  <span className="mb-1.5 block text-xs text-muted">供应商</span>
                  <select
                    className="input"
                    value={draft.provider}
                    onChange={(e) => onProviderChange(cfg, e.target.value)}
                  >
                    {providerOptions.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.label}
                      </option>
                    ))}
                  </select>
                  {provider?.hint && (
                    <span className="mt-1.5 block text-xs leading-relaxed text-muted">
                      {provider.hint}
                    </span>
                  )}
                  {layerUnsupported && (
                    <span className="mt-1 block text-xs text-warn">
                      该供应商未声明承接「{cfg.label}」，保存前请确认模型可用
                    </span>
                  )}
                </label>

                <div className="block">
                  <span className="mb-1.5 block text-xs text-muted">模型名（点击展开候选，也可直接输入）</span>
                  <div className="flex gap-2">
                    <div className="relative flex-1">
                      <input
                        className="input w-full pr-9 font-mono"
                        value={draft.modelName}
                        placeholder={isLocal ? "选择已下载的本地模型" : "如 gpt-4o"}
                        onChange={(e) => {
                          patchDraft(cfg.layer, { modelName: e.target.value });
                          setOpenMenu(cfg.layer);
                        }}
                        onFocus={() => setOpenMenu(cfg.layer)}
                        onKeyDown={(e) => e.key === "Escape" && setOpenMenu(null)}
                      />
                      {/* 展开箭头：datalist 在部分浏览器点击不弹出，这里显式给一个可点区域 */}
                      <button
                        type="button"
                        className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted transition-colors hover:text-ink"
                        title={openMenu === cfg.layer ? "收起候选模型" : "展开候选模型"}
                        onClick={() => setOpenMenu(openMenu === cfg.layer ? null : cfg.layer)}
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M6 9.5l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      </button>

                      {openMenu === cfg.layer && (
                        <>
                          {/* 透明遮罩：点面板外任意处即收起，免用 document 事件监听 */}
                          <div className="fixed inset-0 z-20" onClick={() => setOpenMenu(null)} />
                          <div className="absolute left-0 right-0 top-full z-30 mt-1 max-h-60 overflow-y-auto rounded-btn border border-line bg-card shadow-lg">
                            {filtered.length === 0 ? (
                              <p className="px-3 py-2.5 text-xs leading-relaxed text-muted">
                                {layerBusy === "discover"
                                  ? "正在拉取可用模型…"
                                  : options.length
                                    ? "无匹配候选，将使用手动输入的模型名"
                                    : "暂无候选：填写 API Key 后会自动拉取，也可直接手动输入模型名"}
                              </p>
                            ) : (
                              filtered.map((m) => (
                                <button
                                  key={m.id}
                                  type="button"
                                  className={`flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-brand-light/50 ${
                                    m.id === draft.modelName ? "bg-brand-light/40" : ""
                                  }`}
                                  onClick={() => {
                                    patchDraft(cfg.layer, { modelName: m.id });
                                    setOpenMenu(null);
                                  }}
                                >
                                  <span className="font-mono text-xs text-ink">{m.id}</span>
                                  {m.label && m.label !== m.id && (
                                    <span className="truncate text-xs text-muted">{m.label}</span>
                                  )}
                                  {m.id === draft.modelName && (
                                    <span className="tag ml-auto shrink-0 bg-brand-light text-brand">当前</span>
                                  )}
                                </button>
                              ))
                            )}
                          </div>
                        </>
                      )}
                    </div>
                    <button
                      className="btn-secondary shrink-0 !px-3 !py-2 text-xs"
                      onClick={() => onDiscover(cfg)}
                      disabled={layerBusy !== null}
                      title={
                        isLocal
                          ? "读取项目内已下载的本地模型"
                          : "用当前供应商与 API Key 重新拉取可用模型清单"
                      }
                    >
                      {layerBusy === "discover" ? "拉取中…" : "重新拉取"}
                    </button>
                  </div>
                  {discoverNote[cfg.layer] && (
                    <span className="mt-1.5 block text-xs leading-relaxed text-muted">
                      {discoverNote[cfg.layer]}
                    </span>
                  )}
                </div>

                <label className="block">
                  <span className="mb-1.5 block text-xs text-muted">API Key</span>
                  <input
                    className="input font-mono"
                    type="password"
                    value={draft.apiKey}
                    disabled={noKeyNeeded}
                    placeholder={
                      noKeyNeeded
                        ? provider?.keyPlaceholder || "无需 Key"
                        : cfg.hasKey
                          ? `已配置（${cfg.apiKeyMasked}），留空即不修改`
                          : provider?.keyPlaceholder || "请输入 API Key"
                    }
                    onChange={(e) => patchDraft(cfg.layer, { apiKey: e.target.value })}
                  />
                  {!noKeyNeeded && (
                    <span className="mt-1.5 block text-xs text-muted">
                      填写后自动拉取该账号可用的模型清单（无需点按钮），也可随时手动输入模型名
                    </span>
                  )}
                </label>

                <label className="block">
                  <span className="mb-1.5 block text-xs text-muted">base_url</span>
                  <input
                    className="input font-mono"
                    value={draft.baseUrl}
                    disabled={isLocal}
                    placeholder={
                      isLocal ? "本地推理，无需 base_url" : "选定供应商后自动填入，可改为自建网关"
                    }
                    onChange={(e) => patchDraft(cfg.layer, { baseUrl: e.target.value })}
                  />
                  {!isLocal && provider?.baseUrl && draft.baseUrl !== provider.baseUrl && (
                    <span className="mt-1.5 block text-xs text-muted">
                      已自定义（{provider.label} 默认：{provider.baseUrl}）
                    </span>
                  )}
                </label>
                <label className="block">
                  <span className="mb-1.5 block text-xs text-muted">降级模型（选填）</span>
                  <input
                    className="input font-mono"
                    value={draft.fallbackModel}
                    placeholder="主模型超时/限流时自动切换"
                    onChange={(e) => patchDraft(cfg.layer, { fallbackModel: e.target.value })}
                  />
                </label>
                <div className="flex items-end pb-1">
                  <button
                    className="flex items-center gap-2 text-sm"
                    onClick={() => patchDraft(cfg.layer, { enabled: !draft.enabled })}
                    title="停用后该层任务走降级预案（回退到主模型或排队重试）"
                  >
                    <span
                      className={`relative h-5 w-9 rounded-full transition-colors ${
                        draft.enabled ? "bg-success" : "bg-line"
                      }`}
                    >
                      <span
                        className={`absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
                          draft.enabled ? "translate-x-4" : "translate-x-0"
                        }`}
                      />
                    </span>
                    <span className={draft.enabled ? "text-ink" : "text-muted"}>
                      {draft.enabled ? "启用该层" : "停用该层"}
                    </span>
                  </button>
                </div>
              </div>

              {/* 分层差异化参数 */}
              <div className="mt-4 grid grid-cols-2 gap-4 md:grid-cols-4">
                {Object.keys(draft.params).map((key) => {
                  const meta = LLM_PARAM_META[key];
                  return (
                    <label key={key} className="block">
                      <span className="mb-1.5 block text-xs text-muted">
                        {meta?.label ?? key}
                        {meta?.hint && (
                          <em className="ml-1 not-italic text-muted/70" title={meta.hint}>
                            ⓘ
                          </em>
                        )}
                      </span>
                      <input
                        className="input font-mono"
                        type={meta?.kind === "number" ? "number" : "text"}
                        step={meta?.step}
                        min={meta?.min}
                        max={meta?.max}
                        value={draft.params[key]}
                        onChange={(e) =>
                          patchDraft(cfg.layer, {
                            params: { ...draft.params, [key]: e.target.value },
                          })
                        }
                      />
                    </label>
                  );
                })}
              </div>

              {/* 操作区 */}
              <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-line pt-4">
                <button
                  className="btn-primary !px-4 !py-2 text-sm"
                  onClick={() => onSave(cfg)}
                  disabled={layerBusy !== null}
                >
                  {layerBusy === "save" ? "保存中…" : "保存并热生效"}
                </button>
                <button
                  className="btn-secondary !px-4 !py-2 text-sm"
                  onClick={() => onTest(cfg)}
                  disabled={layerBusy !== null}
                >
                  {layerBusy === "test" ? "测试中…" : "测试连接"}
                </button>
                <button
                  className="btn-secondary !px-4 !py-2 text-sm"
                  onClick={() => onRollback(cfg)}
                  disabled={!canRollback || layerBusy !== null}
                  title={canRollback ? "回滚到上一版配置" : "暂无可回滚的历史版本"}
                >
                  {layerBusy === "rollback" ? "回滚中…" : "回滚上一版"}
                </button>
                <span className="ml-auto text-xs text-muted">
                  {cfg.updatedAt
                    ? `最近更新 ${cfg.updatedAt} · ${cfg.updatedBy}`
                    : "尚未修改，使用默认配置"}
                  {canRollback && ` · 可回滚 ${historyCount[cfg.layer]} 版`}
                </span>
              </div>

              {/* 自测结果 */}
              {test && (
                <div
                  className={`mt-3 rounded-btn px-3.5 py-2.5 text-xs ${
                    test.ok ? "bg-green-50 text-success" : "bg-red-50 text-danger"
                  }`}
                >
                  {test.ok
                    ? `连通正常 · 延迟 ${test.latencyMs}ms${
                        test.tokens ? ` · ${test.tokens} tokens` : ""
                      }${test.detail ? ` · ${test.detail}` : ""}`
                    : `连通失败 · ${test.error}${test.detail ? ` · ${test.detail}` : ""}`}
                </div>
              )}
              {/* 变更提示（如向量维度需重建索引） */}
              {notice && (
                <div className="mt-3 rounded-btn bg-orange-50 px-3.5 py-2.5 text-xs text-warn">
                  {notice}
                </div>
              )}
            </section>
          );
        })}
      </div>

      {/* 审计日志：只记配置修改与连通性自测，分页呈现 */}
      <section className="card mt-6 p-5">
        <header className="flex items-center justify-between">
          <h2 className="text-base font-semibold">变更审计</h2>
          <span className="text-xs text-muted">
            共 {audit.length} 条 · Key 只记掩码
          </span>
        </header>
        {audit.length === 0 ? (
          <p className="mt-4 py-6 text-center text-sm text-muted">暂无变更记录</p>
        ) : (
          <>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="text-muted">
                  <tr className="border-b border-line">
                    <th className="py-2 pr-4 font-normal">时间</th>
                    <th className="py-2 pr-4 font-normal">操作人</th>
                    <th className="py-2 pr-4 font-normal">动作</th>
                    <th className="py-2 pr-4 font-normal">分层</th>
                    <th className="py-2 font-normal">详情</th>
                  </tr>
                </thead>
                <tbody>
                  {audit
                    .slice((auditPage - 1) * AUDIT_PAGE_SIZE, auditPage * AUDIT_PAGE_SIZE)
                    .map((entry, i) => (
                      <tr key={`${entry.at}-${i}`} className="border-b border-line/70 last:border-0">
                        <td className="py-2 pr-4 whitespace-nowrap font-mono text-muted">{entry.at}</td>
                        <td className="py-2 pr-4">{entry.actor}</td>
                        <td className="py-2 pr-4">
                          <span className="tag bg-line/70 text-ink">
                            {AUDIT_ACTION_LABEL[entry.action] ?? entry.action}
                          </span>
                        </td>
                        <td className="py-2 pr-4 font-mono text-muted">{entry.layer}</td>
                        <td className="py-2 text-ink/80">{entry.detail}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
            {auditTotalPages > 1 && (
              <footer className="mt-3 flex items-center justify-between text-xs text-muted">
                <span>
                  第 {auditPage} / {auditTotalPages} 页
                </span>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setAuditPage((p) => Math.max(1, p - 1))}
                    disabled={auditPage <= 1}
                    className="rounded border border-line px-2.5 py-1 disabled:opacity-40 enabled:hover:bg-line/60"
                  >
                    上一页
                  </button>
                  <button
                    type="button"
                    onClick={() => setAuditPage((p) => Math.min(auditTotalPages, p + 1))}
                    disabled={auditPage >= auditTotalPages}
                    className="rounded border border-line px-2.5 py-1 disabled:opacity-40 enabled:hover:bg-line/60"
                  >
                    下一页
                  </button>
                </div>
              </footer>
            )}
          </>
        )}
      </section>

      {toast && (
        <div className="fixed bottom-8 left-1/2 z-50 -translate-x-1/2 rounded-full bg-ink px-5 py-2.5 text-sm text-white shadow-lg">
          {toast}
        </div>
      )}
    </div>
  );
}
