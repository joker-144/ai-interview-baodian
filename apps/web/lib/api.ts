"use client";

/**
 * Mock API 层（一期）：
 * - C 端方法为 async，签名与 FastAPI 接口一一对应，数据持久化在 localStorage；
 * - 管理端（模型配置 / 审计）已切换为直连后端 `/api/admin/*`（见下方 adminFetch），
 *   配置统一由后端落盘 `apps/api/config/llm.json`，不再使用 localStorage。
 */
import {
  DEACTIVATION_COOLING_DAYS,
  DELETION_SCOPES,
  GENERATE_DIMENSIONS,
  MOCK_MASTERED_BASE,
  MOCK_PLANS,
  MOCK_QUESTIONS,
  MOCK_RESUME_ANALYSIS,
  MOCK_SETS,
  MOCK_USER,
  MOCK_USER_SETTINGS,
  MOCK_WEEK_BARS,
  MOCK_WEEK_STATS,
  MOCK_WRONG_BOOK,
  REVIEW_STAGE_LABELS,
} from "./mock-data";
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

const LS = {
  auth: "aib:auth",
  plans: "aib:plans",
  sets: "aib:sets", // QuestionSet[]（支持新建/删除，种子数据来自 MOCK_SETS）
  progress: "aib:progress", // Record<setId, Record<questionId, choice>>
  wrongBook: "aib:wrongBook", // WrongItem[]
  favorites: "aib:favorites", // questionId[]
  resume: "aib:resume", // ResumeAnalysis | null
  adminToken: "aib:adminToken", // string | null（管理端会话，独立于 C 端登录态）
  me: "aib:me", // StoredMe（P14：资料覆盖项 / 偏好 / 注销申请）
} as const;

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

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** 进行中的出题任务数：注销前需校验为 0（与后端 generate_tasks 未完成项同构） */
let generatingCount = 0;

/* ---------------- 账号 ---------------- */

export async function loginByPhone(phone: string, _code: string): Promise<UserProfile> {
  await delay(400);
  write(LS.auth, { phone, at: Date.now() });
  return MOCK_USER;
}

export async function loginByWechat(): Promise<UserProfile> {
  await delay(400);
  write(LS.auth, { wechat: true, at: Date.now() });
  return MOCK_USER;
}

export function isLoggedIn(): boolean {
  return read<{ at: number } | null>(LS.auth, null) !== null;
}

export async function logout(): Promise<void> {
  if (typeof window !== "undefined") window.localStorage.removeItem(LS.auth);
}

export async function getUser(): Promise<UserProfile> {
  return MOCK_USER;
}

/* ---------------- 我的 / 设置（P14，对齐 /api/me*） ---------------- */

/** 存储态：资料只存用户改过的覆盖项，未改字段始终读种子（与后端 state.profile 同构） */
interface StoredMe {
  profile: Partial<UserProfile>;
  settings: UserSettings;
  deactivation: DeactivationInfo | null;
}

function readMe(): StoredMe {
  const saved = read<StoredMe | null>(LS.me, null);
  return {
    profile: saved?.profile ?? {},
    settings: { ...MOCK_USER_SETTINGS, ...(saved?.settings ?? {}) },
    deactivation: saved?.deactivation ?? null,
  };
}

const writeMe = (me: StoredMe) => write(LS.me, me);

const pad = (n: number) => String(n).padStart(2, "0");

function stamp(d: Date): string {
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  );
}

/** Safari 不认 "YYYY-MM-DD HH:mm:ss"，解析前先把日期分隔符换成斜杠 */
function remainingDays(until: string): number {
  const diff = new Date(until.replace(/-/g, "/")).getTime() - Date.now();
  return diff <= 0 ? 0 : Math.ceil(diff / 86400000);
}

function assertActive(me: StoredMe): void {
  if (me.deactivation?.status === "executed") {
    throw new Error("账号已注销，数据不可恢复，无法修改资料或设置");
  }
}

/** 我的页一次拉齐：资料 + 三统计卡 + 扩展计数 + 偏好 + 注销态 */
function buildMe(): MeProfile {
  const me = readMe();
  const profile: UserProfile = { ...MOCK_USER, ...me.profile };
  const items = read<WrongItem[]>(LS.wrongBook, MOCK_WRONG_BOOK);
  return {
    profile,
    settings: me.settings,
    stats: {
      answered: profile.totalAnswered,
      correctRate: profile.correctRate,
      streak: profile.streak,
      pendingReview: items.filter((i) => !i.mastered).length,
      mastered: MOCK_MASTERED_BASE + items.filter((i) => i.mastered).length,
    },
    counts: {
      sets: (read<QuestionSet[] | null>(LS.sets, null) ?? MOCK_SETS).length,
      favorites: read<string[]>(LS.favorites, []).length,
      questions: MOCK_QUESTIONS.length,
    },
    deactivation: me.deactivation
      ? {
          ...me.deactivation,
          remainingDays:
            me.deactivation.status === "executed"
              ? 0
              : remainingDays(me.deactivation.coolingOffUntil),
        }
      : null,
  };
}

export async function getMe(): Promise<MeProfile> {
  await delay(120);
  return buildMe();
}

export async function updateProfile(patch: ProfileUpdate): Promise<MeProfile> {
  await delay(260);
  const me = readMe();
  assertActive(me);
  const changes: Record<string, unknown> = {};
  Object.entries(patch).forEach(([k, v]) => {
    if (v !== undefined && v !== "") changes[k] = v;
  });
  if (!Object.keys(changes).length) throw new Error("没有需要更新的资料项");
  // 改姓名未单独指定头像字时，头像跟随姓名首字（避免头像与姓名长期不一致）
  const name = changes.name;
  if (typeof name === "string" && !changes.avatarText) changes.avatarText = name.trim()[0];
  writeMe({ ...me, profile: { ...me.profile, ...(changes as Partial<UserProfile>) } });
  return buildMe();
}

export async function updateSettings(patch: Partial<UserSettings>): Promise<MeProfile> {
  await delay(200);
  const me = readMe();
  assertActive(me);
  const changes: Partial<UserSettings> = {};
  if (patch.reviewReminderEnabled !== undefined) {
    changes.reviewReminderEnabled = patch.reviewReminderEnabled;
  }
  if (patch.reviewReminderTime !== undefined) {
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(patch.reviewReminderTime)) {
      throw new Error("提醒时间格式应为 HH:mm（24 小时制）");
    }
    changes.reviewReminderTime = patch.reviewReminderTime;
  }
  if (!Object.keys(changes).length) throw new Error("没有需要更新的设置项");
  writeMe({ ...me, settings: { ...me.settings, ...changes } });
  return buildMe();
}

/** 注销申请：进入 7 天冷静期（对齐 DELETE /api/me） */
export async function requestDeactivation(reason = ""): Promise<DeactivationInfo> {
  await delay(320);
  if (generatingCount > 0) {
    throw new Error(`存在进行中的出题任务（${generatingCount} 个），请等待完成后再申请注销`);
  }
  const me = readMe();
  if (me.deactivation?.status === "executed") throw new Error("账号已注销，数据不可恢复");
  if (me.deactivation) {
    // 重复申请不重置冷静期，避免靠反复申请拖延清理
    throw new Error(`已有进行中的注销申请，冷静期至 ${me.deactivation.coolingOffUntil}`);
  }
  const now = new Date();
  const info: DeactivationInfo = {
    status: "cooling_off",
    requestedAt: stamp(now),
    coolingOffUntil: stamp(new Date(now.getTime() + DEACTIVATION_COOLING_DAYS * 86400000)),
    remainingDays: DEACTIVATION_COOLING_DAYS,
    reason: reason.trim(),
    scopes: [...DELETION_SCOPES],
    revocable: true,
  };
  writeMe({ ...me, deactivation: info });
  return info;
}

/** 冷静期内撤回注销：数据保持原样 */
export async function cancelDeactivation(): Promise<MeProfile> {
  await delay(260);
  const me = readMe();
  if (!me.deactivation) throw new Error("当前没有进行中的注销申请");
  if (me.deactivation.status === "executed") {
    throw new Error("注销已执行，数据已清理，无法撤回");
  }
  writeMe({ ...me, deactivation: null });
  return buildMe();
}

/**
 * 执行数据清理（真实系统由定时任务在冷静期到期后触发）。
 * Mock 层无定时任务，force=true 用于跳过到期校验以验证「申请 → 冷静期 → 清理」链路。
 */
export async function executeDeactivation(force = false): Promise<DeletionResult> {
  await delay(500);
  const me = readMe();
  if (!me.deactivation) throw new Error("当前没有进行中的注销申请");
  if (me.deactivation.status === "executed") throw new Error("清理已执行，请勿重复调用");
  const remaining = remainingDays(me.deactivation.coolingOffUntil);
  if (remaining > 0 && !force) {
    throw new Error(`仍在冷静期内（剩余 ${remaining} 天，至 ${me.deactivation.coolingOffUntil}）；该期间可撤回`);
  }
  const deleted = {
    progress: Object.keys(read<Record<string, Record<string, string>>>(LS.progress, {})).length,
    wrongBook: read<WrongItem[]>(LS.wrongBook, MOCK_WRONG_BOOK).length,
    favorites: read<string[]>(LS.favorites, []).length,
    questionSets: (read<QuestionSet[] | null>(LS.sets, null) ?? MOCK_SETS).length,
    plans: read<PlanTask[]>(LS.plans, MOCK_PLANS).length,
    resume: read<ResumeAnalysis | null>(LS.resume, null) ? 1 : 0,
  };
  write(LS.progress, {});
  write(LS.wrongBook, []);
  write(LS.favorites, []);
  write(LS.sets, []);
  write(LS.plans, []);
  write(LS.resume, null);
  // 账号标记为已注销：统计归零、资料不再可读（与后端 execute_deactivation 同构）
  writeMe({
    profile: {
      name: "已注销用户",
      avatarText: "注",
      targetRole: "—",
      years: 0,
      streak: 0,
      totalAnswered: 0,
      correctRate: 0,
      phone: "",
      wechatBound: false,
    },
    settings: me.settings,
    deactivation: { ...me.deactivation, status: "executed", remainingDays: 0, revocable: false },
  });
  const total = Object.values(deleted).reduce((a, b) => a + b, 0);
  return {
    ok: true,
    executedAt: stamp(new Date()),
    deleted,
    detail: `已清理 ${total} 项数据（含简历原文件删除），账号不可恢复`,
  };
}

/* ---------------- 首页 / 计划 ---------------- */

export async function getPlans(): Promise<PlanTask[]> {
  const saved = read<PlanTask[] | null>(LS.plans, null);
  if (saved) return saved;
  write(LS.plans, MOCK_PLANS);
  return MOCK_PLANS;
}

export async function togglePlan(taskId: string): Promise<PlanTask[]> {
  const plans = await getPlans();
  const next = plans.map((p) => (p.id === taskId ? { ...p, done: !p.done } : p));
  write(LS.plans, next);
  return next;
}

export async function addPlan(title: string): Promise<PlanTask[]> {
  const plans = await getPlans();
  const next: PlanTask[] = [
    ...plans,
    {
      id: `plan-${Date.now()}`,
      type: "practice",
      title,
      estMinutes: 15,
      done: false,
    },
  ];
  write(LS.plans, next);
  return next;
}

export async function getWeekOverview() {
  return { bars: MOCK_WEEK_BARS, stats: MOCK_WEEK_STATS };
}

/* ---------------- 题库中心 ---------------- */

export async function getSets(): Promise<QuestionSet[]> {
  const saved = read<QuestionSet[] | null>(LS.sets, null);
  if (saved) return saved;
  write(LS.sets, MOCK_SETS);
  return MOCK_SETS;
}

export async function getSet(setId: string): Promise<QuestionSet | null> {
  const sets = await getSets();
  return sets.find((s) => s.id === setId) ?? null;
}

/** 新建自定义题集（初始为空，题目由引擎生成或手动添加） */
export async function createSet(
  title: string,
  source: QuestionSet["source"] = "resume",
): Promise<QuestionSet> {
  const sets = await getSets();
  const set: QuestionSet = {
    id: `set-${Date.now()}`,
    source,
    title,
    questionCount: 0,
    updatedAt: "刚刚创建",
  };
  write(LS.sets, [set, ...sets]);
  return set;
}

/** 删除题集：同步清理该题集的刷题进度与相关错题 */
export async function deleteSet(setId: string): Promise<void> {
  const sets = await getSets();
  write(
    LS.sets,
    sets.filter((s) => s.id !== setId),
  );

  const progress = read<Record<string, Record<string, string>>>(LS.progress, {});
  delete progress[setId];
  write(LS.progress, progress);

  const items = read<WrongItem[]>(LS.wrongBook, MOCK_WRONG_BOOK);
  write(
    LS.wrongBook,
    items.filter((w) => w.setId !== setId),
  );
}

export async function getQuestions(setId: string): Promise<Question[]> {
  return MOCK_QUESTIONS.filter((q) => q.setId === setId);
}

export async function getQuestion(questionId: string): Promise<Question | null> {
  return MOCK_QUESTIONS.find((q) => q.id === questionId) ?? null;
}

/** 题集刷题进度：返回 { answeredCount, answers } */
export async function getProgress(
  setId: string,
): Promise<{ answeredCount: number; answers: Record<string, string> }> {
  const all = read<Record<string, Record<string, string>>>(LS.progress, {});
  const answers = all[setId] ?? {};
  return { answeredCount: Object.keys(answers).length, answers };
}

export interface SubmitResult {
  correct: boolean;
  answer: string;
  autoAddedToWrongBook: boolean;
}

/** 提交作答：判分 + 错题自动收录（对齐 POST /api/practice/submit） */
export async function submitAnswer(setId: string, questionId: string, choice: string): Promise<SubmitResult> {
  const q = await getQuestion(questionId);
  if (!q) throw new Error("question not found");

  const all = read<Record<string, Record<string, string>>>(LS.progress, {});
  all[setId] = { ...(all[setId] ?? {}), [questionId]: choice };
  write(LS.progress, all);

  const correct = choice === q.answer;
  let autoAddedToWrongBook = false;
  if (!correct) {
    autoAddedToWrongBook = !(await isInWrongBook(questionId));
    await addToWrongBook(questionId, "concept");
  }
  return { correct, answer: q.answer, autoAddedToWrongBook };
}

export async function resetProgress(setId: string): Promise<void> {
  const all = read<Record<string, Record<string, string>>>(LS.progress, {});
  delete all[setId];
  write(LS.progress, all);
}

/* ---------------- 收藏 ---------------- */

export async function getFavorites(): Promise<string[]> {
  return read<string[]>(LS.favorites, []);
}

export async function toggleFavorite(questionId: string): Promise<boolean> {
  const favs = await getFavorites();
  const idx = favs.indexOf(questionId);
  const next = idx >= 0 ? favs.filter((f) => f !== questionId) : [...favs, questionId];
  write(LS.favorites, next);
  return idx < 0;
}

/* ---------------- 错题本 ---------------- */

export async function getWrongBook(reason?: WrongReason | "all"): Promise<WrongItem[]> {
  const items = read<WrongItem[]>(LS.wrongBook, MOCK_WRONG_BOOK);
  const active = items.filter((w) => !w.mastered);
  if (!reason || reason === "all") return active;
  return active.filter((w) => w.reason === reason);
}

export async function getWrongStats() {
  const items = read<WrongItem[]>(LS.wrongBook, MOCK_WRONG_BOOK);
  const active = items.filter((w) => !w.mastered);
  return {
    pending: active.length,
    dueToday: active.filter((w) => w.nextReviewLabel === "今天").length,
    mastered: MOCK_MASTERED_BASE + items.filter((w) => w.mastered).length,
  };
}

/** 艾宾浩斯五档队列：按错题实际所处阶段聚合 */
export async function getReviewQueue() {
  const items = read<WrongItem[]>(LS.wrongBook, MOCK_WRONG_BOOK);
  return REVIEW_STAGE_LABELS.map((label, stage) => ({
    label,
    count: items.filter((w) => !w.mastered && w.stage === stage).length,
  }));
}

export async function isInWrongBook(questionId: string): Promise<boolean> {
  const items = read<WrongItem[]>(LS.wrongBook, MOCK_WRONG_BOOK);
  return items.some((w) => w.questionId === questionId && !w.mastered);
}

export async function addToWrongBook(questionId: string, reason: WrongReason): Promise<void> {
  const items = read<WrongItem[]>(LS.wrongBook, MOCK_WRONG_BOOK);
  const existing = items.find((w) => w.questionId === questionId);
  if (existing) {
    existing.mastered = false;
    existing.wrongCount += 1;
    existing.reason = reason;
    existing.lastWrongAt = "刚刚";
    existing.stage = 0;
    existing.nextReviewLabel = "今天";
    existing.reviewStreak = 0;
  } else {
    const q = await getQuestion(questionId);
    items.unshift({
      questionId,
      setId: q?.setId ?? "set-1",
      reason,
      wrongCount: 1,
      lastWrongAt: "刚刚",
      stage: 0,
      nextReviewLabel: "今天",
      mastered: false,
      reviewStreak: 0,
    });
  }
  write(LS.wrongBook, items);
}

export async function updateWrongReason(questionId: string, reason: WrongReason): Promise<void> {
  const items = read<WrongItem[]>(LS.wrongBook, MOCK_WRONG_BOOK);
  const it = items.find((w) => w.questionId === questionId);
  if (it) {
    it.reason = reason;
    write(LS.wrongBook, items);
  }
}

const STAGE_LABELS = REVIEW_STAGE_LABELS;

/** 复习作答：答对推进艾宾浩斯阶段（连对 3 次提前毕业），答错回到第 1 档 */
export async function submitReview(
  questionId: string,
  correct: boolean,
): Promise<{ mastered: boolean }> {
  const items = read<WrongItem[]>(LS.wrongBook, MOCK_WRONG_BOOK);
  const it = items.find((w) => w.questionId === questionId);
  if (!it) return { mastered: false };
  if (correct) {
    it.reviewStreak += 1;
    it.stage = Math.min(it.stage + 1, STAGE_LABELS.length - 1);
    it.nextReviewLabel = STAGE_LABELS[it.stage];
    if (it.reviewStreak >= 3 || it.stage >= STAGE_LABELS.length - 1) {
      it.mastered = true;
    }
  } else {
    it.reviewStreak = 0;
    it.stage = 0;
    it.wrongCount += 1;
    it.lastWrongAt = "刚刚";
    it.nextReviewLabel = "今天";
  }
  write(LS.wrongBook, items);
  return { mastered: it.mastered };
}

/* ---------------- 引擎 A：简历解析 + 流式出题 ---------------- */

export async function uploadAndParseResume(_fileName: string): Promise<ResumeAnalysis> {
  await delay(1200); // 模拟解析耗时
  write(LS.resume, MOCK_RESUME_ANALYSIS);
  return MOCK_RESUME_ANALYSIS;
}

export async function getResumeAnalysis(): Promise<ResumeAnalysis | null> {
  return read<ResumeAnalysis | null>(LS.resume, null);
}

/**
 * 模拟 SSE 流式出题（对齐 GET /api/question-sets/{task_id}/stream）：
 * 异步生成器逐步推送进度，前端边生成边渲染。
 */
export async function* streamGenerate(total = 80): AsyncGenerator<GenerateProgress> {
  generatingCount += 1;
  try {
    let generated = 0;
    const perDimension = Math.ceil(total / GENERATE_DIMENSIONS.length);
    for (const dim of GENERATE_DIMENSIONS) {
      const target = Math.min(generated + perDimension, total);
      while (generated < target) {
        await delay(120);
        generated = Math.min(generated + Math.ceil(Math.random() * 4), target);
        yield { generated, total, currentDimension: dim, done: false };
      }
    }
    yield { generated: total, total, currentDimension: "完成", done: true };
  } finally {
    // 中途关页 / 抛错也要释放计数，否则注销会被永久拦住
    generatingCount = Math.max(0, generatingCount - 1);
  }
}

/* ---------------- 管理端：模型配置（直连后端 /api/admin/*） ---------------- */

/**
 * 后端地址（开发态）。
 *
 * 管理端不再走 localStorage Mock：API Key / 模型配置统一由后端落盘到
 * `apps/api/config/llm.json`（真实配置被 .gitignore 忽略不入仓，仓内为同目录
 * `.example` 模板，Key 全空）；配置 / 历史快照 / 审计在重启后均保留。
 * 因此管理端页面依赖 API 服务运行（apps/api，默认 8000 端口）。
 */
const API_BASE = "http://127.0.0.1:8000";

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
