"use client";

/**
 * API 层：
 * - C 端与 FastAPI 后端一一对应（apps/api，默认 127.0.0.1:8000），数据存后端内存态；
 * - 管理端（模型配置 / 审计）同样直连 `/api/admin/*`，Key 由后端混淆落盘
 *   `apps/api/config/llm.json`，前端不保存任何密钥明文；
 * - 本地只保留：登录态 token、管理端令牌、以及注销前的出题任务计数。
 */
import type {
  ActiveGenerateTask,
  AuditEntry,
  BossBrowserStartResult,
  DeactivationInfo,
  DeletionResult,
  ExamListItem,
  ExamPaper,
  ExamReport,
  ExamState,
  GenerateProgress,
  InterviewAnswerResult,
  InterviewCapabilities,
  InterviewListItem,
  InterviewMode,
  InterviewReportPayload,
  InterviewSession,
  DailyPractice,
  DailyProgressResult,
  JobCard,
  JobDetailResult,
  JobMap,
  JobSearchResult,
  JobsStatus,
  LlmConfig,
  LlmConfigUpdate,
  LlmLayer,
  LlmProvider,
  LlmTestResult,
  LocalModelList,
  MeProfile,
  ModelDiscoverResult,
  NotificationItem,
  PlanList,
  ProfileUpdate,
  Question,
  QuestionSet,
  PipelineAddInput,
  PipelineBoard,
  PipelineCard,
  PipelinePatchInput,
  RemedialResult,
  ResumeAnalysis,
  ResumeCheckup,
  ResumeVersion,
  UserProfile,
  UserSettings,
  WeekOverview,
  WeeklyReport,
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

/** C 端请求封装：自动携带 JWT（Authorization: Bearer），统一错误提示（后端 detail 直出给用户） */
async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const isForm = typeof FormData !== "undefined" && init?.body instanceof FormData;
  const auth = read<StoredAuth | null>(LS.auth, null);
  const authHeaders: Record<string, string> = auth?.token
    ? { Authorization: `Bearer ${auth.token}` }
    : {};
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: isForm
        ? authHeaders
        : { "Content-Type": "application/json", ...authHeaders, ...(init?.headers ?? {}) },
    });
  } catch {
    throw new Error("无法连接后端服务（apps/api，默认 8000 端口），请确认服务已启动");
  }
  if (!res.ok) {
    // 会话过期 / 无效：清掉本地登录态，让页面回登录页（登录接口自身的 401 不受影响）
    if (res.status === 401 && auth) {
      if (typeof window !== "undefined") window.localStorage.removeItem(LS.auth);
    }
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

/** 账号密码登录（一期主链路）：后端校验 PBKDF2 哈希并签发 JWT */
export async function loginByAccount(account: string, password: string): Promise<UserProfile> {
  const res = await post<{ token: string; user: UserProfile }>("/api/auth/login", { account, password });
  write(LS.auth, { token: res.token, user: res.user } satisfies StoredAuth);
  return res.user;
}

/** 注册并直接登录：后端建号后即签发 token（注册即登录） */
export async function registerAccount(
  account: string,
  password: string,
  name?: string,
): Promise<UserProfile> {
  const res = await post<{ token: string; user: UserProfile }>("/api/auth/register", {
    account,
    password,
    ...(name ? { name } : {}),
  });
  write(LS.auth, { token: res.token, user: res.user } satisfies StoredAuth);
  return res.user;
}

/** 短信验证码登录（预留，后端返回 501「暂未开通」） */
export async function loginByPhone(phone: string, code: string): Promise<UserProfile> {
  const res = await post<{ token: string; user: UserProfile }>("/api/auth/phone-login", { phone, code });
  write(LS.auth, { token: res.token, user: res.user } satisfies StoredAuth);
  return res.user;
}

/** 微信登录（预留，后端返回 501「暂未开通」） */
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

/** 当前登录用户（读本地登录态缓存；未登录返回 null，不请求后端） */
export async function getUser(): Promise<UserProfile | null> {
  const stored = read<StoredAuth | null>(LS.auth, null);
  return stored?.user ?? null;
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

/* ---------------- 首页 / 计划 / 周报（二期） ---------------- */

/** 今日学习计划：当日无计划时后端惰性生成（AI 措辞，失败回落规则） */
export async function getPlans(): Promise<PlanList> {
  return apiFetch<PlanList>("/api/plans");
}

export async function togglePlan(taskId: string): Promise<PlanList> {
  return post<PlanList>(`/api/plans/${taskId}/toggle`);
}

export async function addPlan(title: string): Promise<PlanList> {
  return post<PlanList>("/api/plans", { title });
}

export async function deletePlan(taskId: string): Promise<PlanList> {
  return apiFetch<PlanList>(`/api/plans/${taskId}`, { method: "DELETE" });
}

/** 本周柱状图 + 三卡（从作答事件真实聚合） */
export async function getWeekOverview(): Promise<WeekOverview> {
  return apiFetch<WeekOverview>("/api/stats/week");
}

/** 学习周报（趋势 / 薄弱知识点 TOP5 / 下周建议） */
export async function getWeeklyReport(): Promise<WeeklyReport> {
  return apiFetch<WeeklyReport>("/api/reports/weekly");
}

/* ---------------- 模拟考试（二期批 2） ---------------- */

/** 组卷：指定题集抽客观题（最多 40 题，限时 = 题数 × 90 秒） */
export async function createExam(setId: string): Promise<ExamPaper> {
  return post<ExamPaper>("/api/exams", { setId });
}

/** 历史列表（含 running，入口据此弹「恢复上次考试」） */
export async function getExams(): Promise<ExamListItem[]> {
  return apiFetch<ExamListItem[]>("/api/exams");
}

/** 考试现场（running/paused 原样返回，断网/刷新恢复基础） */
export async function getExamState(examId: string): Promise<ExamState> {
  return apiFetch<ExamState>(`/api/exams/${examId}`);
}

/** 单题作答：即时落库但不回传对错（考试态无判分回显；PUT 对齐后端路由） */
export async function answerExam(
  examId: string,
  questionId: string,
  choice: string,
  timeSec = 0,
): Promise<{ ok: boolean; answered: number }> {
  return apiFetch<{ ok: boolean; answered: number }>(`/api/exams/${examId}/answer`, {
    method: "PUT",
    body: JSON.stringify({ questionId, choice, timeSec }),
  });
}

export async function pauseExam(examId: string): Promise<void> {
  await post(`/api/exams/${examId}/pause`);
}

export async function resumeExam(examId: string): Promise<void> {
  await post(`/api/exams/${examId}/resume`);
}

export async function abandonExam(examId: string): Promise<void> {
  await post(`/api/exams/${examId}/abandon`);
}

/** 交卷判分（「只交已答」口径由前端确认层保证） */
export async function submitExam(examId: string): Promise<{ score: number }> {
  return post<{ score: number }>(`/api/exams/${examId}/submit`);
}

/** 模考报告：分数 / 维度分 / 分桶百分位 / 薄弱点 / 历史趋势 */
export async function getExamReport(examId: string): Promise<ExamReport> {
  return apiFetch<ExamReport>(`/api/exams/${examId}/report`);
}

/** 一键补强：按薄弱点从题库抽同标签题生成补强卷题集 */
export async function remedialExam(examId: string): Promise<RemedialResult> {
  return post<RemedialResult>(`/api/exams/${examId}/remedial`);
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

// ---------------- 简历多版本 / 体检 / AI 优化（批 3） ----------------

/** 多版本简历列表（version 降序；analysis 内含体检报告，旧记录可能缺） */
export async function getResumes(): Promise<ResumeVersion[]> {
  return apiFetch<ResumeVersion[]>("/api/resumes");
}

/** 体检报告；旧记录缺 checkup 时后端惰性补算并写回 */
export async function getResumeCheckup(resumeId: string): Promise<ResumeCheckup> {
  const res = await apiFetch<{ checkup: ResumeCheckup }>(
    `/api/resumes/${resumeId}/checkup`,
  );
  return res.checkup;
}

/** AI 一键优化：按体检结论主模型重写，返回优化版新版本（version+1） */
export async function optimizeResume(resumeId: string): Promise<ResumeAnalysis> {
  return post<ResumeAnalysis>(`/api/resumes/${resumeId}/optimize`);
}

/** 删除一个简历版本（最新版被删时后端自动回退到次新版） */
export async function deleteResume(resumeId: string): Promise<void> {
  await apiFetch<{ ok: boolean }>(`/api/resumes/${resumeId}`, { method: "DELETE" });
}

/** DOCX 下载：带 JWT 拉取二进制并触发浏览器保存（a[download] 直链带不了 Authorization） */
export async function downloadResumeDocx(resumeId: string, fileName: string): Promise<void> {
  const auth = read<StoredAuth | null>(LS.auth, null);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/resumes/${resumeId}/download`, {
      headers: auth?.token ? { Authorization: `Bearer ${auth.token}` } : {},
    });
  } catch {
    throw new Error("无法连接后端服务（apps/api，默认 8000 端口），请确认服务已启动");
  }
  if (!res.ok) {
    let message = `下载失败（HTTP ${res.status}）`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // 非 JSON 错误体时用状态码描述
    }
    throw new Error(message);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName.endsWith(".docx") ? fileName : `${fileName}.docx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ---------------- 通知中心 / 数据导出（批 4） ----------------

export async function getNotifications(): Promise<{ unread: number; items: NotificationItem[] }> {
  return apiFetch<{ unread: number; items: NotificationItem[] }>("/api/notifications");
}

export async function readNotification(id: string): Promise<void> {
  await apiFetch<{ ok: boolean }>(`/api/notifications/${id}/read`, { method: "PUT" });
}

export async function readAllNotifications(): Promise<void> {
  await apiFetch<{ updated: number }>("/api/notifications/read-all", { method: "PUT" });
}

/** 数据导出：带 JWT 拉取聚合 JSON 并触发浏览器保存（个人数据副本，第五章） */
export async function exportMyData(): Promise<void> {
  const auth = read<StoredAuth | null>(LS.auth, null);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/me/export`, {
      headers: auth?.token ? { Authorization: `Bearer ${auth.token}` } : {},
    });
  } catch {
    throw new Error("无法连接后端服务（apps/api，默认 8000 端口），请确认服务已启动");
  }
  if (!res.ok) {
    let message = `导出失败（HTTP ${res.status}）`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // 非 JSON 错误体时用状态码描述
    }
    throw new Error(message);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `个人数据导出-${new Date().toISOString().slice(0, 10).replaceAll("-", "")}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** 触发出题任务，返回 task_id（真实调用主模型，进度走 streamGenerate）。
 *  setId：续作场景（中断恢复「继续补齐」）向已有题集追加生成。 */
export async function startGenerate(count: number, setId?: string): Promise<string> {
  const res = await post<{ taskId: string }>("/api/question-sets/generate", {
    source: "resume",
    settings: { count, ...(setId ? { setId } : {}) },
  });
  return res.taskId;
}

/** 当前用户最近的未完成出题任务（无活跃任务时 taskId 为 null）。
 *  进入生成页先查这里：running → 恢复进度条订阅；interrupted → 提示继续补齐。 */
export async function getActiveGenerateTask(): Promise<ActiveGenerateTask> {
  return apiFetch<ActiveGenerateTask>("/api/question-sets/generate/active");
}

/** SSE 断线重连上限与间隔：覆盖网络抖动与后端重启窗口（30 次 × 2s） */
const SSE_MAX_RETRIES = 30;
const SSE_RETRY_DELAY_MS = 2000;

/**
 * 订阅出题进度（对齐 GET /api/question-sets/{task_id}/stream）。
 *
 * 用 fetch + ReadableStream 解析 SSE（EventSource 不支持 GET 之外的定制且无法关闭重连），
 * 客户端断开不影响后端生成。断线自动重连：先轮询一次终态（后端重启时任务已转中断态），
 * 未结束则重新订阅 SSE，直到收到 done 事件或任务结束。
 */
export async function* streamGenerate(taskId: string): AsyncGenerator<GenerateProgress> {
  generatingCount += 1;
  try {
    const auth = read<StoredAuth | null>(LS.auth, null);
    const headers: Record<string, string> = auth?.token
      ? { Authorization: `Bearer ${auth.token}` }
      : {};
    for (let retries = 0; ; ) {
      try {
        const res = await fetch(`${API_BASE}/api/question-sets/${taskId}/stream`, { headers });
        if (!res.ok || !res.body) throw new Error(`订阅出题进度失败（HTTP ${res.status}）`);

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const { value, done } = await reader.read();
          if (done) break; // 流关闭：正常结束已通过 done 事件 return，否则视为断线
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
        throw new Error("SSE 连接中断"); // EOF 且未收到 done 事件 → 走重连
      } catch (err) {
        if (retries >= SSE_MAX_RETRIES) throw err;
        retries += 1;
        await new Promise((r) => setTimeout(r, SSE_RETRY_DELAY_MS));
        // 重连前先轮询一次：任务已结束（含后端重启后的中断态）则直接透出终态
        try {
          const p = await getGenerateProgress(taskId);
          if (p.done) {
            yield p;
            return;
          }
        } catch {
          // 轮询也失败（后端仍在重启窗口）：继续下一轮重连
        }
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

/* ---------------- 岗位检索（三期，引擎 B） ---------------- */

/** CLI 安装状态 + Boss 登录态（未登录时 /jobs 页展示扫码引导卡） */
export async function getJobsStatus(): Promise<JobsStatus> {
  return apiFetch<JobsStatus>("/api/jobs/status");
}

/** 按需拉起 Boss 专用 Chrome（持久化 profile + CDP 9222）；仅用户显式点击时调用 */
export async function startBossBrowser(): Promise<BossBrowserStartResult> {
  return post<BossBrowserStartResult>("/api/jobs/browser/start");
}

/** 关键词检索岗位（后端分层采样 + 缓存 1 天；同关键词+城市当日命中缓存不重查） */
export async function searchJobs(keyword: string, city = ""): Promise<JobSearchResult> {
  return post<JobSearchResult>("/api/jobs/search", { keyword, city });
}

/** 单岗位全量 JD（缓存 7 天） */
export async function getJobDetail(securityId: string): Promise<JobDetailResult> {
  return apiFetch<JobDetailResult>(`/api/jobs/${encodeURIComponent(securityId)}/detail`);
}

/** 触发引擎 B 出题（岗位检索通道：settings 携带 keyword/city/difficulty/withAnswer） */
export async function startJobGenerate(params: {
  keyword: string;
  city?: string;
  count: number;
  difficulty: string;
  withAnswer: boolean;
}): Promise<string> {
  const res = await post<{ taskId: string }>("/api/question-sets/generate", {
    source: "job_search",
    settings: {
      keyword: params.keyword,
      city: params.city ?? "",
      count: params.count,
      difficulty: params.difficulty,
      withAnswer: params.withAnswer,
    },
  });
  return res.taskId;
}

/** 触发引擎 C（子集）出题（单岗位专属通道：看板卡上生成，settings 携带 securityId/keyword/city） */
export async function startJobDetailGenerate(params: {
  securityId: string;
  keyword?: string;
  city?: string;
  count: number;
  difficulty: string;
  withAnswer: boolean;
}): Promise<string> {
  const res = await post<{ taskId: string }>("/api/question-sets/generate", {
    source: "jd_target",
    settings: {
      securityId: params.securityId,
      keyword: params.keyword ?? "",
      city: params.city ?? "",
      count: params.count,
      difficulty: params.difficulty,
      withAnswer: params.withAnswer,
    },
  });
  return res.taskId;
}

/** 岗位考点地图（未生成过时后端 404，由调用方提示） */
export async function getJobMap(keyword: string, city = ""): Promise<JobMap> {
  const query = new URLSearchParams({ keyword, city });
  return apiFetch<JobMap>(`/api/jobs/map?${query.toString()}`);
}

/* ---------------- 每日一练（三期） ---------------- */

/** 当日每日一练（首次访问后端惰性生成：薄弱知识点 60% + 随机 40%，共 10 题） */
export async function getDailyPractice(): Promise<DailyPractice> {
  return apiFetch<DailyPractice>("/api/daily-practice");
}

/** 标记一题完成（判分本体走 practice/submit；全部完成时后端自动打卡） */
export async function markDailyProgress(questionId: string): Promise<DailyProgressResult> {
  return post<DailyProgressResult>("/api/daily-practice/progress", { questionId });
}

/* ---------------- 求职看板（四期，job_pipeline 四列状态机） ---------------- */

/** 四列看板 + 趋势统计（后端顺带惰性触发面试临近提醒） */
export async function getPipeline(): Promise<PipelineBoard> {
  return apiFetch<PipelineBoard>("/api/pipeline");
}

/** 加入看板：zhipin 卡传 securityId+keyword（自动回填匹配分+挂题集）；手动卡传岗位字段 */
export async function addPipelineCard(input: PipelineAddInput): Promise<PipelineCard> {
  return post<PipelineCard>("/api/pipeline", input);
}

/** 流转阶段 / 改备注 / 设面试日期 / 换挂题集 */
export async function updatePipelineCard(
  cardId: string,
  patch: PipelinePatchInput,
): Promise<PipelineCard> {
  return apiFetch<PipelineCard>(`/api/pipeline/${encodeURIComponent(cardId)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

/** 移入回收站（软删） */
export async function deletePipelineCard(cardId: string): Promise<void> {
  await apiFetch<{ ok: boolean }>(`/api/pipeline/${encodeURIComponent(cardId)}`, {
    method: "DELETE",
  });
}

/** 回收站列表 */
export async function getPipelineTrash(): Promise<{ cards: PipelineCard[] }> {
  return apiFetch<{ cards: PipelineCard[] }>("/api/pipeline/trash");
}

/** 从回收站还原 */
export async function restorePipelineCard(cardId: string): Promise<PipelineCard> {
  return post<PipelineCard>(`/api/pipeline/${encodeURIComponent(cardId)}/restore`);
}

/** 彻底删除（回收站永久删除） */
export async function purgePipelineCard(cardId: string): Promise<void> {
  await apiFetch<{ ok: boolean }>(`/api/pipeline/${encodeURIComponent(cardId)}/permanent`, {
    method: "DELETE",
  });
}

/* ---------------- 语音模拟面试（五期，文档 3.9） ---------------- */

/** 二进制响应封装（TTS 音频 / 录音回放）：带 JWT 拉 Blob，错误体仍走后端 detail 直出 */
async function apiFetchBlob(path: string, init?: RequestInit, errPrefix = "请求"): Promise<Blob> {
  const auth = read<StoredAuth | null>(LS.auth, null);
  const authHeaders: Record<string, string> = auth?.token
    ? { Authorization: `Bearer ${auth.token}` }
    : {};
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...authHeaders, ...(init?.headers ?? {}) },
    });
  } catch {
    throw new Error("无法连接后端服务（apps/api，默认 8000 端口），请确认服务已启动");
  }
  if (!res.ok) {
    let message = `${errPrefix}失败（HTTP ${res.status}）`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // 非 JSON 错误体时用状态码描述
    }
    throw new Error(message);
  }
  return res.blob();
}

/** 语音能力探测：asrAvailable=false → 降级文字输入；ttsAvailable=false → 不展示读题 */
export async function getInterviewCapabilities(): Promise<InterviewCapabilities> {
  return apiFetch<InterviewCapabilities>("/api/interview-sessions/capabilities");
}

/** 历史面试列表（倒序；据此展示「查看复盘 / 继续」入口） */
export async function getInterviewSessions(): Promise<InterviewListItem[]> {
  return apiFetch<InterviewListItem[]>("/api/interview-sessions");
}

/** 创建面试会话：后端按「目标岗位 + 简历」实时动态出题（数秒），返回整场题目 */
export async function createInterviewSession(params: {
  mode: InterviewMode;
  targetJob: string;
  count: number;
  recordAudio: boolean;
}): Promise<InterviewSession> {
  return post<InterviewSession>("/api/interview-sessions", params);
}

/** 取会话现场（刷新 / 断网恢复：题目 + 已答明细原样回填） */
export async function getInterviewSession(sessionId: string): Promise<InterviewSession> {
  return apiFetch<InterviewSession>(`/api/interview-sessions/${encodeURIComponent(sessionId)}`);
}

/** 题目/追问文字 → 面试官语音（云端 Qwen3-TTS），返回可播放 WAV Blob */
export async function synthesizeInterviewTts(
  sessionId: string,
  params: { seq?: number; text?: string },
): Promise<Blob> {
  return apiFetchBlob(
    `/api/interview-sessions/${encodeURIComponent(sessionId)}/tts`,
    { method: "POST", body: JSON.stringify({ seq: params.seq ?? -1, text: params.text ?? "" }) },
    "语音合成",
  );
}

/** 提交单题作答：有 file 走后端 ASR 转写；无 file 只传 transcript（文字降级）→ light 层即时评分 */
export async function submitInterviewAnswer(
  sessionId: string,
  input: { seq: number; file?: Blob; transcript?: string; durationSec?: number; retainAudio?: boolean },
): Promise<InterviewAnswerResult> {
  const form = new FormData();
  form.append("seq", String(input.seq));
  if (input.file) form.append("file", input.file, `answer-${input.seq}.wav`);
  form.append("transcript", input.transcript ?? "");
  form.append("durationSec", String(input.durationSec ?? 0));
  form.append("retainAudio", input.retainAudio === false ? "false" : "true");
  return apiFetch<InterviewAnswerResult>(
    `/api/interview-sessions/${encodeURIComponent(sessionId)}/answers`,
    { method: "POST", body: form },
  );
}

/** 提交一层追问的作答（录音或文字），存为候选人轮次 */
export async function submitInterviewFollowup(
  sessionId: string,
  answerId: string,
  input: { file?: Blob; transcript?: string; durationSec?: number; retainAudio?: boolean },
): Promise<{ ok: boolean; seq: number; transcript: string }> {
  const form = new FormData();
  if (input.file) form.append("file", input.file, `followup-${answerId}.wav`);
  form.append("transcript", input.transcript ?? "");
  form.append("durationSec", String(input.durationSec ?? 0));
  form.append("retainAudio", input.retainAudio === false ? "false" : "true");
  return apiFetch<{ ok: boolean; seq: number; transcript: string }>(
    `/api/interview-sessions/${encodeURIComponent(sessionId)}/answers/${encodeURIComponent(answerId)}/followup`,
    { method: "POST", body: form },
  );
}

/** 结束面试：primary 层生成复盘报告（四维雷达 + 总评 + 改进建议） */
export async function finishInterview(sessionId: string): Promise<InterviewReportPayload> {
  return post<InterviewReportPayload>(
    `/api/interview-sessions/${encodeURIComponent(sessionId)}/finish`,
  );
}

/** 读复盘报告（含每题文字稿与追问轮次） */
export async function getInterviewReport(sessionId: string): Promise<InterviewReportPayload> {
  return apiFetch<InterviewReportPayload>(
    `/api/interview-sessions/${encodeURIComponent(sessionId)}/report`,
  );
}

/** 录音回放：仅本人、仅开启留存时；返回 WAV Blob（供 <audio> 播放） */
export async function fetchInterviewAudio(
  sessionId: string,
  answerId: string,
): Promise<Blob> {
  return apiFetchBlob(
    `/api/interview-sessions/${encodeURIComponent(sessionId)}/answers/${encodeURIComponent(answerId)}/audio`,
    undefined,
    "录音获取",
  );
}

/** 删除面试会话（连带作答/追问/录音）；合规「可删」口径 */
export async function deleteInterviewSession(sessionId: string): Promise<void> {
  await apiFetch<{ ok: boolean }>(
    `/api/interview-sessions/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
  );
}
