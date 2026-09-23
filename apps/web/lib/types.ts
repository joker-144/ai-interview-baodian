/** 统一题型协议（对齐产品文档 3.2.1）与一期领域模型 */

export type QuestionSource = "resume" | "job_search" | "jd_target" | "mock_interview";

export type QuestionType =
  | "single_choice"
  | "multi_choice"
  | "judge"
  | "short_answer"
  | "scenario";

/** 错因三分法 */
export type WrongReason = "concept" | "misread" | "blind_spot";

export const WRONG_REASON_LABEL: Record<WrongReason, string> = {
  concept: "概念不清",
  misread: "审题失误",
  blind_spot: "知识盲区",
};

export const SOURCE_LABEL: Record<QuestionSource, string> = {
  resume: "简历生成",
  job_search: "岗位检索",
  jd_target: "JD定制",
  mock_interview: "模拟面试",
};

export interface UserProfile {
  name: string;
  avatarText: string;
  targetRole: string;
  years: number;
  streak: number;
  totalAnswered: number;
  correctRate: number;
}

export interface PlanTask {
  id: string;
  type: "practice" | "review" | "interview" | "jd_set" | "resume_check";
  title: string;
  estMinutes: number;
  done: boolean;
}

export interface QuestionSet {
  id: string;
  source: QuestionSource;
  title: string;
  questionCount: number;
  updatedAt: string; // 展示文案，如「更新于 2 小时前」
}

export interface Question {
  id: string;
  setId: string;
  type: QuestionType;
  category: string;
  stem: string;
  options: string[]; // 客观题选项
  answer: string; // 如 "B"
  referenceAnswer: string;
  explanation: string;
  knowledgeTags: string[];
  difficulty: 1 | 2 | 3;
  /** 全站答对率（0~100），低样本（<100 次作答）为 null 不展示 */
  siteCorrectRate: number | null;
}

export interface WrongItem {
  questionId: string;
  setId: string;
  reason: WrongReason;
  wrongCount: number;
  lastWrongAt: string; // 展示文案，如「昨天」
  /** 艾宾浩斯阶段索引 0~4 对应 1/2/4/7/15 天 */
  stage: number;
  nextReviewLabel: string; // 展示文案，如「今天」「第 4 天」
  mastered: boolean;
  /** 复习连续答对次数，满 3 提前毕业 */
  reviewStreak: number;
}

export interface ResumeAnalysis {
  fileName: string;
  years: number;
  targetRole: string;
  estimatedCount: number;
  dimensions: { label: string; score: number }[];
}

export interface GenerateProgress {
  generated: number;
  total: number;
  currentDimension: string;
  done: boolean;
}
