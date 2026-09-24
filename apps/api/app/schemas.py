"""Pydantic 模型：字段与前端 apps/web/lib/types.ts 一一对应。"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

QuestionSource = Literal["resume", "job_search", "jd_target", "mock_interview"]
WrongReason = Literal["concept", "misread", "blind_spot"]


# ---------- 用户 / 账号 ----------

class UserProfile(BaseModel):
    name: str
    avatarText: str
    targetRole: str
    years: int
    streak: int
    totalAnswered: int
    correctRate: int
    phone: str = ""          # 脱敏回显（138****6021），仅我的页使用
    wechatBound: bool = False


class PhoneLoginRequest(BaseModel):
    phone: str = Field(pattern=r"^1\d{10}$")
    code: str = Field(min_length=4, max_length=6)


class TokenResponse(BaseModel):
    token: str
    user: UserProfile


# ---------- 我的 / 设置（P14 一期最小版） ----------

TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


class UserSettings(BaseModel):
    """一期只开放复习提醒；学习报告 / 通知管理 / 数据导出随承载功能落二期。"""

    reviewReminderEnabled: bool = True
    reviewReminderTime: str = Field(default="20:00", pattern=TIME_PATTERN)


class UserSettingsUpdate(BaseModel):
    reviewReminderEnabled: bool | None = None
    reviewReminderTime: str | None = Field(default=None, pattern=TIME_PATTERN)


class ProfileUpdate(BaseModel):
    """个人资料可改项（头像以首字呈现，不开放上传）；未传字段视为不修改。"""

    name: str | None = Field(default=None, min_length=1, max_length=20)
    avatarText: str | None = Field(default=None, min_length=1, max_length=2)
    targetRole: str | None = Field(default=None, min_length=1, max_length=30)
    years: int | None = Field(default=None, ge=0, le=50)


class DeactivationInfo(BaseModel):
    status: Literal["cooling_off", "executed"]
    requestedAt: str
    coolingOffUntil: str
    remainingDays: int
    reason: str
    scopes: list[str]
    revocable: bool


class DeactivateRequest(BaseModel):
    reason: str = Field(default="", max_length=200)


class DeletionResult(BaseModel):
    ok: bool
    executedAt: str
    deleted: dict[str, int]   # 各类数据实际清理条数
    detail: str


class MeProfile(BaseModel):
    """我的页一次拉齐：资料 + 三统计卡 + 扩展计数 + 偏好 + 注销态。"""

    profile: UserProfile
    settings: UserSettings
    stats: dict[str, int]     # answered / correctRate / streak / pendingReview / mastered
    counts: dict[str, int]    # sets / favorites / notes
    deactivation: DeactivationInfo | None = None


# ---------- 今日学习计划 ----------

class PlanTask(BaseModel):
    id: str
    type: Literal["practice", "review", "interview", "jd_set", "resume_check"]
    title: str
    estMinutes: int
    done: bool


class PlanAddRequest(BaseModel):
    title: str = Field(min_length=1, max_length=60)


# ---------- 题库 / 题目 ----------

class QuestionSet(BaseModel):
    id: str
    source: QuestionSource
    title: str
    questionCount: int
    updatedAt: str


class SetCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=40)
    source: QuestionSource = "resume"


class Question(BaseModel):
    id: str
    setId: str
    type: Literal["single_choice", "multi_choice", "judge", "short_answer", "scenario"]
    category: str
    stem: str
    options: list[str]
    answer: str
    referenceAnswer: str
    explanation: str
    knowledgeTags: list[str]
    difficulty: Literal[1, 2, 3]
    siteCorrectRate: Optional[int] = None  # 低样本（<100 次作答）为 null 不展示


class SiteStats(BaseModel):
    """全站答对率（独立刷新入口，低样本保护见合规红线第 4 条）。"""

    questionId: str
    answeredCount: int
    correctRate: Optional[int] = None  # 作答数 <100 时为 null，前端不展示


class ProgressOut(BaseModel):
    answeredCount: int
    answers: dict[str, str]


class SubmitRequest(BaseModel):
    setId: str
    questionId: str
    choice: str = Field(pattern=r"^[A-F]$")


class SubmitResult(BaseModel):
    correct: bool
    answer: str
    autoAddedToWrongBook: bool


# ---------- 错题本 ----------

class WrongItem(BaseModel):
    questionId: str
    setId: str
    reason: WrongReason
    wrongCount: int
    lastWrongAt: str
    stage: int  # 艾宾浩斯阶段 0~4 对应 1/2/4/7/15 天
    nextReviewLabel: str
    mastered: bool
    reviewStreak: int


class WrongAddRequest(BaseModel):
    questionId: str
    reason: WrongReason = "concept"


class WrongReasonUpdate(BaseModel):
    reason: WrongReason


class ReviewSubmitRequest(BaseModel):
    questionId: str
    correct: bool


class ReviewSubmitResult(BaseModel):
    mastered: bool


class ReviewQueueItem(BaseModel):
    label: str
    count: int


# ---------- 引擎 A：简历解析与出题 ----------

class ResumeAnalysis(BaseModel):
    fileName: str
    years: int
    targetRole: str
    estimatedCount: int
    dimensions: list["DimensionScore"]


class ResumeUploadRequest(BaseModel):
    fileName: str = Field(min_length=1, max_length=200)


class DimensionScore(BaseModel):
    label: str
    score: int


class GenerateRequest(BaseModel):
    source: QuestionSource = "resume"
    resumeId: Optional[str] = None
    settings: dict = Field(default_factory=dict)


class GenerateTaskOut(BaseModel):
    taskId: str


class GenerateProgress(BaseModel):
    generated: int
    total: int
    currentDimension: str
    done: bool


# ---------- 管理端：模型配置（文档 4.6.1） ----------

LlmLayer = Literal["primary", "light", "vision", "embedding", "voice"]


class LlmConfigOut(BaseModel):
    layer: LlmLayer
    label: str
    usage: str
    provider: str
    modelName: str
    apiKeyMasked: str  # 仅掩码（sk-****abcd），永不返回明文
    hasKey: bool
    baseUrl: str
    params: dict
    fallbackModel: str = ""
    enabled: bool = True
    updatedAt: str = ""
    updatedBy: str = ""
    notice: Optional[str] = None  # 如「向量维度已变更，需重建 pgvector 索引」


class LlmConfigUpdate(BaseModel):
    provider: Optional[str] = None
    modelName: Optional[str] = None
    # 不回传或回传掩码 = 未修改（文档 4.6.1 第 2 条）
    apiKey: Optional[str] = None
    baseUrl: Optional[str] = None
    params: Optional[dict] = None
    fallbackModel: Optional[str] = None
    enabled: Optional[bool] = None


class LlmTestResult(BaseModel):
    ok: bool
    latencyMs: Optional[int] = None
    tokens: Optional[int] = None
    error: Optional[str] = None
    detail: Optional[str] = None  # 成功时的补充信息（如本地推理维度 / 样本数）


class ProviderOut(BaseModel):
    """供应商清单项：选定后前端自动回填 baseUrl（可手改）。"""

    id: str
    label: str
    kind: str  # cloud | selfhost | local | custom
    baseUrl: str
    requiresKey: bool
    keyPlaceholder: str = ""
    capabilities: list[str] = []  # 可承接的分层：primary/light/vision/embedding/voice
    hint: str = ""


class ModelOption(BaseModel):
    """模型候选项（实时拉取 / 本地清单 / 常用兜底）。"""

    id: str
    label: str = ""
    ownedBy: str = ""
    kind: str = ""


class ModelDiscoverRequest(BaseModel):
    provider: str
    baseUrl: str = ""  # 缺省用供应商默认地址（已手改时传回自定义值）
    # 明文 Key 仅在本次请求内使用：不落库、不写日志（审计只记掩码）
    apiKey: str = ""
    layer: Optional[LlmLayer] = None  # apiKey 为空时，回落到该分层已存密钥


class ModelDiscoverOut(BaseModel):
    ok: bool
    source: str  # remote | local | static | none
    models: list[ModelOption] = []
    error: Optional[str] = None


class LocalModelOut(BaseModel):
    """已下载到项目内的本地模型（apps/api/models/）。"""

    modelId: str
    label: str = ""
    kind: str = ""
    repo: str = ""
    dimensions: Optional[int] = None
    path: str = ""
    sizeMB: float = 0
    downloadedAt: str = ""
    desc: str = ""


class LocalModelListOut(BaseModel):
    modelsDir: str
    downloadCommand: str
    updatedAt: str = ""
    models: list[LocalModelOut] = []


class AuditEntry(BaseModel):
    at: str
    actor: str
    action: str
    layer: str
    detail: str
