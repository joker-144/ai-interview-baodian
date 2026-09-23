"use client";

/**
 * Mock API 层（一期）：
 * - 所有方法为 async，签名与未来 FastAPI 接口一一对应，后续仅需替换实现为 fetch 调用；
 * - 用户态数据（刷题进度 / 错题本 / 收藏 / 登录态）持久化在 localStorage。
 */
import {
  GENERATE_DIMENSIONS,
  MOCK_MASTERED_BASE,
  MOCK_PLANS,
  MOCK_QUESTIONS,
  MOCK_RESUME_ANALYSIS,
  MOCK_SETS,
  MOCK_USER,
  MOCK_WEEK_BARS,
  MOCK_WEEK_STATS,
  MOCK_WRONG_BOOK,
  REVIEW_STAGE_LABELS,
} from "./mock-data";
import type {
  GenerateProgress,
  PlanTask,
  Question,
  QuestionSet,
  ResumeAnalysis,
  UserProfile,
  WrongItem,
  WrongReason,
} from "./types";

const LS = {
  auth: "aib:auth",
  plans: "aib:plans",
  progress: "aib:progress", // Record<setId, Record<questionId, choice>>
  wrongBook: "aib:wrongBook", // WrongItem[]
  favorites: "aib:favorites", // questionId[]
  resume: "aib:resume", // ResumeAnalysis | null
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
  return MOCK_SETS;
}

export async function getSet(setId: string): Promise<QuestionSet | null> {
  return MOCK_SETS.find((s) => s.id === setId) ?? null;
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
}
