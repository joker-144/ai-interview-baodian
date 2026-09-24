"use client";

/**
 * API 层：
 * - C 端与 FastAPI 后端一一对应（apps/api，默认 127.0.0.1:8000），数据存后端内存态；
 * - 管理端（模型配置 / 审计）同样直连 `/api/admin/*`，Key 由后端混淆落盘
 *   `apps/api/config/llm.json`，前端不保存任何密钥明文；
 * - 本地只保留：登录态 token、管理端令牌、以及注销前的出题任务计数。
 */
import type {
  AuditEntry,
  DeactivationInfo,
  DeletionResult,
  GenerateProgress,
  LlmConfig,
  LlmConfigUpdate,
  LlmLayer,
  LlmProvider,
  LlmTestResult,
  LocalModelList,
  MeProfile,
  ModelDiscoverResult,
  PlanTask,
  ProfileUpdate,
  Question,
  QuestionSet,
  ResumeAnalysis,
  UserProfile,
  UserSettings,
  WrongItem,
  WrongReason,
} from "./types";

const API_BASE = "http://127.0.0.1:8000";

const LS = {
  auth: "aib:auth", // { token, user }
  adminToken: "aib:adminToken", // string | null（管理端会话，独立于 C 端登录态）
} as const;

interface StoredAuth {
  token: string;
  user: UserProfile;
}

function read<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(key, JSON.stringify(value));
}

/** C 端请求封装：统一错误提示（后端 detail 直出给用户） */
async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const isForm = typeof FormData !== "undefined" && init?.body instanceof FormData;
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: isForm ? undefined : { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new Error("无法连接后端服务（apps/api，默认 8000 端口），请确认服务已启动");
  }
  if (!res.ok) {
    let message = `请求失败（HTTP ${res.status}）`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") message = body.detail;
      else if (body.detail) message = JSON.stringify(body.detail);
    } catch {
      // 非 JSON 错误体时用状态码描述
    }
    throw new Error(message);
  }
  return (await res.json()) as T;
}

const post = <T,>(path: string, body?: unknown) =>
  apiFetch<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });

/** 进行中的出题任务数：注销前需校验为 0（与后端 generate_tasks 未完成项同构） */
let generatingCount = 0;

/* ---------------- 账号 ---------------- */

export async function loginByPhone(phone: string, code: string): Promise<UserProfile> {
  const res = await post<{ token: string; user: UserProfile }>("/api/auth/phone-login", { phone, code });
  write(LS.auth, { token: res.token, user: res.user } satisfies StoredAuth);
  return res.user;
}

export async function loginByWechat(): Promise<UserProfile> {
  const res = await post<{ token: string; user: UserProfile }>("/api/auth/wechat-login");
  write(LS.auth, { token: res.token, user: res.user } satisfies StoredAuth);
  return res.user;
}

export function isLoggedIn(): boolean {
  return read<StoredAuth | null>(LS.auth, null) !== null;
}

export async function logout(): Promise<void> {
  if (typeof window !== "undefined") window.localStorage.removeItem(LS.auth);
}

export async function getUser(): Promise<UserProfile> {
  const stored = read<StoredAuth | null>(LS.auth, null);
  if (stored?.user) return stored.user;
  return apiFetch<UserProfile>("/api/auth/me");
}

/* ---------------- 我的 / 设置（P14，对齐 /api/me*） ---------------- */

export async function getMe(): Promise<MeProfile> {
  return apiFetch<MeProfile>("/api/me");
}

export async function updateProfile(patch: ProfileUpdate): Promise<MeProfile> {
  return apiFetch<MeProfile>("/api/me", { method: "PUT", body: JSON.stringify(patch) });
}

export async function updateSettings(patch: Partial<UserSettings>): Promise<MeProfile> {
  return apiFetch<MeProfile>("/api/me/settings", { method: "PUT", body: JSON.stringify(patch) });
}

/** 注销申请：进入 7 天冷静期 */
export async function requestDeactivation(reason = ""): Promise<DeactivationInfo> {
  if (generatingCount > 0) {
    throw new Error(`存在进行中的出题任务（${generatingCount} 个），请等待完成后再申请注销`);
  }
  return apiFetch<DeactivationInfo>("/api/me", {
    method: "DELETE",
    body: JSON.stringify({ reason }),
  });
}

/** 冷静期内撤回注销：数据保持原样 */
export async function cancelDeactivation(): Promise<MeProfile> {
  return post<MeProfile>("/api/me/deactivation/cancel");
}

/**
 * 执行数据清理（真实系统由定时任务在冷静期到期后触发）。
 * `force=true` 用于跳过到期校验以验证「申请 → 冷静期 → 清理」链路。
 */
export async function executeDeactivation(force = false): Promise<DeletionResult> {
  return post<DeletionResult>(`/api/me/deactivation/execute?force=${force ? "true" : "false"}`);
}

/* ---------------- 首页 / 计划 ---------------- */

export async function getPlans(): Promise<PlanTask[]> {
  return apiFetch<PlanTask[]>("/api/plans");
}

export async function togglePlan(taskId: string): Promise<PlanTask[]> {
  await post<PlanTask>(`/api/plans/${taskId}/toggle`);
  return getPlans();
}

export async function addPlan(title: string): Promise<PlanTask[]> {
  await apiFetch<PlanTask>("/api/plans", { method: "POST", body: JSON.stringify({ title }) });
  return getPlans();
}

export async function getWeekOverview() {
  return apiFetch<{ bars: { day: string; value: number }[]; stats: { answered: number; correctRate: number; pendingReview: number } }>(
    "/api/stats/week",
  );
}

/* ---------------- 题库中心 ---------------- */

export async function getSets(): Promise<QuestionSet[]> {
  return apiFetch<QuestionSet[]>("/api/question-sets");
}

export async function getSet(setId: string): Promise<QuestionSet | null> {
  try {
    return await apiFetch<QuestionSet>(`/api/question-sets/${setId}`);
  } catch {
    return null;
  }
}

/** 新建自定义题集（初始为空，题目由引擎生成或手动添加） */
export async function createSet(
  title: string,
  source: QuestionSet["source"] = "resume",
): Promise<QuestionSet> {
  return post<QuestionSet>("/api/question-sets", { title, source });
}

/** 删除题集：同步清理该题集的刷题进度与相关错题 */
export async function deleteSet(setId: string): Promise<void> {
  await apiFetch<{ ok: boolean }>(`/api/question-sets/${setId}`, { method: "DELETE" });
}

export async function getQuestions(setId: string): Promise<Question[]> {
  return apiFetch<Question[]>(`/api/questions?setId=${encodeURIComponent(setId)}`);
}

export async function getQuestion(questionId: string): Promise<Question | null> {
  try {
    return await apiFetch<Question>(`/api/questions/${questionId}`);
  } catch {
    return null;
  }
}

/** 题集刷题进度：返回 { answeredCount, answers } */
export async function getProgress(
  setId: string,
): Promise<{ answeredCount: number; answers: Record<string, string> }> {
  return apiFetch<{ answeredCount: number; answers: Record<string, string> }>(`/api/progress/${setId}`);
}

export interface SubmitResult {
  correct: boolean;
  answer: string;
  autoAddedToWrongBook: boolean;
}

/** 提交作答：判分 + 错题自动收录 */
export async function submitAnswer(
  setId: string,
  questionId: string,
  choice: string,
): Promise<SubmitResult> {
  return post<SubmitResult>("/api/practice/submit", { setId, questionId, choice });
}

export async function resetProgress(setId: string): Promise<void> {
  await post("/api/progress/reset", { setId });
}

/* ---------------- 收藏 ---------------- */

export async function getFavorites(): Promise<string[]> {
  return apiFetch<string[]>("/api/favorites");
}

export async function toggleFavorite(questionId: string): Promise<boolean> {
  const res = await post<{ favorited: boolean }>(`/api/favorites/${questionId}/toggle`);
  return res.favorited;
}

/* ---------------- 错题本 ---------------- */

export async function getWrongBook(reason?: WrongReason | "all"): Promise<WrongItem[]> {
  const query = reason && reason !== "all" ? `?reason=${reason}` : "";
  return apiFetch<WrongItem[]>(`/api/wrong-book${query}`);
}

export async function getWrongStats() {
  return apiFetch<{ pending: number; dueToday: number; mastered: number }>("/api/wrong-book/stats");
}

/** 艾宾浩斯五档队列：按错题实际所处阶段聚合 */
export async function getReviewQueue() {
  return apiFetch<{ label: string; count: number }[]>("/api/wrong-book/review-queue");
}

export async function isInWrongBook(questionId: string): Promise<boolean> {
  const items = await getWrongBook();
  return items.some((w) => w.questionId === questionId);
}

export async function addToWrongBook(questionId: string, reason: WrongReason): Promise<void> {
  await post<WrongItem>("/api/wrong-book", { questionId, reason });
}

export async function updateWrongReason(questionId: string, reason: WrongReason): Promise<void> {
  await apiFetch<WrongItem>(`/api/wrong-book/${questionId}/reason`, {
    method: "PATCH",
    body: JSON.stringify({ reason }),
  });
}

/** 复习作答：答对推进艾宾浩斯阶段（连对 3 次提前毕业），答错回到第 1 档 */
export async function submitReview(
  questionId: string,
  correct: boolean,
): Promise<{ mastered: boolean }> {
  return post<{ mastered: boolean }>("/api/wrong-book/review", { questionId, correct });
}

/* ---------------- 引擎 A：简历解析 + 流式出题 ---------------- */

/** 上传简历并解析（multipart 真上传，后端抽文本 + 主模型结构化，约 10~30s） */
export async function uploadAndParseResume(file: File): Promise<ResumeAnalysis> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<ResumeAnalysis>("/api/resumes", { method: "POST", body: form });
}

export async function getResumeAnalysis(): Promise<ResumeAnalysis | null> {
  return apiFetch<ResumeAnalysis | null>("/api/resumes/latest");
}

/** 触发出题任务，返回 task_id（真实调用主模型，进度走 streamGenerate） */
export async function startGenerate(count: number): Promise<string> {
  const res = await post<{ taskId: string }>("/api/question-sets/generate", {
    source: "resume",
    settings: { count },
  });
  return res.taskId;
}

/**
 * 订阅出题进度（对齐 GET /api/question-sets/{task_id}/stream）。
 *
 * 用 fetch + ReadableStream 解析 SSE（EventSource 不支持 GET 之外的定制且无法关闭重连），
 * 客户端断开不影响后端生成，可用 getGenerateProgress 轮询兜底。
 */
export async function* streamGenerate(taskId: string): AsyncGenerator<GenerateProgress> {
  generatingCount += 1;
  try {
    const res = await fetch(`${API_BASE}/api/question-sets/${taskId}/stream`);
    if (!res.ok || !res.body) throw new Error(`订阅出题进度失败（HTTP ${res.status}）`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() ?? "";
      for (const chunk of chunks) {
        if (chunk.startsWith("event: done")) return;
        const line = chunk.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        yield JSON.parse(line.slice(6)) as GenerateProgress;
      }
    }
  } finally {
    // 中途关页 / 抛错也要释放计数，否则注销会被永久拦住
    generatingCount = Math.max(0, generatingCount - 1);
  }
}

/** 轮询兜底：SSE 断开重连时读取进度 */
export async function getGenerateProgress(taskId: string): Promise<GenerateProgress> {
  return apiFetch<GenerateProgress>(`/api/question-sets/${taskId}/progress`);
}

/* ---------------- 管理端：模型配置（直连后端 /api/admin/*） ---------------- */

/** 管理端请求封装：自动带 X-Admin-Token；401 时清掉本地登录态（页面回登录页） */
async function adminFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = read<string | null>(LS.adminToken, null);
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-Admin-Token": token } : {}),
    },
  });
  if (res.status === 401) {
    write(LS.adminToken, null);
    throw new Error("管理口令无效或已过期，请重新登录");
  }
  if (!res.ok) {
    let message = `请求失败（HTTP ${res.status}）`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // 非 JSON 错误体时用状态码描述
    }
    throw new Error(message);
  }
  return (await res.json()) as T;
}

/** 管理口令登录（独立鉴权，不复用 C 端账号体系）：拿口令试调管理端接口验证 */
export async function adminLogin(token: string): Promise<boolean> {
  write(LS.adminToken, token.trim());
  try {
    await adminFetch("/api/admin/providers");
    return true;
  } catch {
    write(LS.adminToken, null);
    return false;
  }
}

export async function adminLogout(): Promise<void> {
  write(LS.adminToken, null);
}

export function isAdminLoggedIn(): boolean {
  return Boolean(read<string | null>(LS.adminToken, null));
}

export async function getLlmConfigs(): Promise<LlmConfig[]> {
  return adminFetch<LlmConfig[]>("/api/admin/llm-config");
}

/** 保存单层配置：回传掩码 / 留空 = 不修改 Key；维度变更返回 notice（后端热生效并落盘） */
export async function updateLlmConfig(
  layer: LlmLayer,
  patch: LlmConfigUpdate,
): Promise<LlmConfig & { notice?: string }> {
  return adminFetch(`/api/admin/llm-config/${layer}`, {
    method: "PUT",
    body: JSON.stringify(patch),
  });
}

/** 连通性自测：本地模型真跑一次向量化，云端按配置完整性判定 */
export async function testLlmConfig(layer: LlmLayer): Promise<LlmTestResult> {
  return adminFetch(`/api/admin/llm-config/${layer}/test`, { method: "POST", body: "{}" });
}

/** 回滚到上一版（历史栈弹出一条覆盖当前），无历史时后端返回 400 */
export async function rollbackLlmConfig(
  layer: LlmLayer,
): Promise<LlmConfig & { notice?: string }> {
  return adminFetch(`/api/admin/llm-config/${layer}/rollback`, {
    method: "POST",
    body: "{}",
  });
}

export async function getAuditLog(): Promise<AuditEntry[]> {
  return adminFetch<AuditEntry[]>("/api/admin/audit-log");
}

/** 历史快照数（控制回滚按钮可用态） */
export async function getLlmHistoryCount(layer: LlmLayer): Promise<number> {
  const res = await adminFetch<{ count: number }>(`/api/admin/llm-config/${layer}/history`);
  return res.count;
}

/* ---------------- 管理端：供应商与模型发现（对齐 /api/admin/providers 等） ---------------- */

/** 供应商清单：页面选中后自动回填 baseUrl，并按 capabilities 提示能否承接该分层 */
export async function getProviders(): Promise<LlmProvider[]> {
  return adminFetch<LlmProvider[]>("/api/admin/providers");
}

/** 项目内已下载的本地模型（对应后端 apps/api/models/manifest.json） */
export async function getLocalModels(): Promise<LocalModelList> {
  return adminFetch<LocalModelList>("/api/admin/local-models");
}

/**
 * 按供应商 + Key 拉取可用模型清单（后端 `POST /api/admin/models/discover`）。
 * 失败时后端回落常用候选并带 error；页面始终允许手动输入模型名。
 * 安全：apiKey 仅用于本次上游请求，不写入任何持久化存储，审计只记掩码。
 */
export async function discoverModels(params: {
  provider: string;
  baseUrl?: string;
  apiKey?: string;
  layer?: LlmLayer;
}): Promise<ModelDiscoverResult> {
  return adminFetch("/api/admin/models/discover", {
    method: "POST",
    body: JSON.stringify(params),
  });
}
