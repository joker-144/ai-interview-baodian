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
  /** 脱敏手机号（138****6021），仅我的页展示 */
  phone?: string;
  wechatBound?: boolean;
}

/** 一期只开放复习提醒；学习报告 / 通知管理 / 数据导出随承载功能落二期 */
export interface UserSettings {
  reviewReminderEnabled: boolean;
  /** HH:mm，24 小时制 */
  reviewReminderTime: string;
}

export type DeactivationStatus = "cooling_off" | "executed";

export interface DeactivationInfo {
  status: DeactivationStatus;
  requestedAt: string;
  coolingOffUntil: string;
  remainingDays: number;
  reason: string;
  /** 注销会删除的数据范围（申请前向用户明示） */
  scopes: string[];
  revocable: boolean;
}

/** 我的页一次拉齐：资料 + 三统计卡 + 扩展计数 + 偏好 + 注销态 */
export interface MeProfile {
  profile: UserProfile;
  settings: UserSettings;
  stats: {
    answered: number;
    correctRate: number;
    streak: number;
    pendingReview: number;
    mastered: number;
  };
  counts: {
    sets: number;
    favorites: number;
    questions: number;
  };
  deactivation: DeactivationInfo | null;
}

export interface ProfileUpdate {
  name?: string;
  avatarText?: string;
  targetRole?: string;
  years?: number;
}

export interface DeletionResult {
  ok: boolean;
  executedAt: string;
  /** 各类数据实际清理条数 */
  deleted: Record<string, number>;
  detail: string;
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
  updatedAt: string; // 真实更新时间（YYYY-MM-DD HH:MM）
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
  /** 被结构校验 / 答案二次校验 / 去重淘汰的题量 */
  dropped?: number;
  /** 非空表示任务失败（部分或全部批次），直接展示给用户 */
  error?: string | null;
  /** 本次生成落库的题集 id，完成后可跳转 */
  setId?: string | null;
}

/* ---------------- 管理端：模型配置（产品文档 4.6.1） ---------------- */

/** 四类分层模型 + 语音，对应架构图 LLM 层 */
export type LlmLayer = "primary" | "light" | "vision" | "embedding" | "voice";

export interface LlmConfig {
  layer: LlmLayer;
  label: string;
  usage: string;
  provider: string;
  modelName: string;
  /** 仅掩码（sk-****abcd），接口永不返回明文 */
  apiKeyMasked: string;
  hasKey: boolean;
  baseUrl: string;
  params: Record<string, number | string>;
  fallbackModel: string;
  enabled: boolean;
  updatedAt: string;
  updatedBy: string;
}

export interface LlmConfigUpdate {
  provider?: string;
  modelName?: string;
  /** 留空或回传掩码 = 不修改 */
  apiKey?: string;
  baseUrl?: string;
  params?: Record<string, number | string>;
  fallbackModel?: string;
  enabled?: boolean;
}

export interface LlmTestResult {
  ok: boolean;
  latencyMs?: number;
  tokens?: number;
  error?: string;
  /** 成功时的补充信息（如本地推理维度 / 相似度校验） */
  detail?: string;
}

/** 供应商：选定即自动回填 baseUrl（可手改），capabilities 决定能承接哪些分层 */
export interface LlmProvider {
  id: string;
  label: string;
  /** cloud=云端 API / selfhost=本机自托管 / local=项目内置权重 / custom=自定义网关 */
  kind: "cloud" | "selfhost" | "local" | "custom";
  baseUrl: string;
  requiresKey: boolean;
  keyPlaceholder: string;
  capabilities: LlmLayer[];
  hint: string;
}

export interface ModelOption {
  id: string;
  label?: string;
  ownedBy?: string;
  kind?: string;
}

/** 模型发现结果：source=remote 实时拉取 / local 本地清单 / static 常用候选兜底 / none 无候选 */
export interface ModelDiscoverResult {
  ok: boolean;
  source: "remote" | "local" | "static" | "none";
  models: ModelOption[];
  error?: string | null;
}

/** 项目内已下载的本地模型（apps/api/models/） */
export interface LocalModelInfo {
  modelId: string;
  label: string;
  kind: string;
  repo: string;
  dimensions?: number | null;
  path: string;
  sizeMB: number;
  downloadedAt: string;
  desc: string;
}

export interface LocalModelList {
  modelsDir: string;
  downloadCommand: string;
  updatedAt: string;
  models: LocalModelInfo[];
}

export interface AuditEntry {
  at: string;
  actor: string;
  action: string;
  layer: string;
  detail: string;
}

/** 审计动作中文名：除「模型发现」外全部打点（与后端 AUDIT_ACTIONS 同构） */
export const AUDIT_ACTION_LABEL: Record<string, string> = {
  update_config: "修改配置",
  test_conn: "连通性自测",
  rollback_config: "版本回滚",
  migrate_model: "模型迁移",
};

/** 分层差异化参数的中文标签与输入类型（未收录的 key 直接展示原名、按文本处理） */
export const LLM_PARAM_META: Record<
  string,
  { label: string; kind: "number" | "text"; step?: number; min?: number; max?: number; hint?: string }
> = {
  temperature: { label: "温度", kind: "number", step: 0.1, min: 0, max: 2, hint: "出题建议 0.3~0.5，校验类建议 0" },
  maxTokens: { label: "最大 tokens", kind: "number", step: 256, min: 1 },
  timeoutSec: { label: "超时（秒）", kind: "number", step: 5, min: 1 },
  concurrency: { label: "并发上限", kind: "number", step: 1, min: 1, hint: "超出后排队，避免触发供应商限流" },
  dimensions: {
    label: "向量维度",
    kind: "number",
    step: 256,
    min: 1,
    hint: "变更后需重建 pgvector 索引",
  },
  sampleRate: { label: "采样率（Hz）", kind: "number", step: 1000, min: 1 },
  ttsVoice: { label: "TTS 音色", kind: "text" },
  ttsModel: { label: "TTS 模型", kind: "text" },
};
