"""一期核心数据表定义（SQLAlchemy 2.0 声明式）。

当前运行时使用 store.py 的内存实现，本文件为接入 PostgreSQL 时的表结构基线，
字段对齐产品文档 4.4（一期范围节选；pgvector embedding 列在二期启用）。
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True)
    wx_openid: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    file_key: Mapped[str] = mapped_column(String(256))  # 对象存储私有桶 key
    parsed_json: Mapped[str] = mapped_column(Text)  # LLM 结构化解析结果
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class QuestionSet(Base):
    __tablename__ = "question_sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    source: Mapped[str] = mapped_column(String(20))  # resume | job_search | jd_target | mock_interview
    resume_id: Mapped[str | None] = mapped_column(ForeignKey("resumes.id"))
    security_id: Mapped[str | None] = mapped_column(String(64))  # 引擎 B/C 岗位溯源
    title: Mapped[str] = mapped_column(String(120))
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default="ready")
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    set_id: Mapped[str] = mapped_column(ForeignKey("question_sets.id"))
    type: Mapped[str] = mapped_column(String(20), default="single_choice")
    category: Mapped[str] = mapped_column(String(30))
    stem: Mapped[str] = mapped_column(Text)
    options_json: Mapped[str] = mapped_column(Text)  # JSON 数组
    answer: Mapped[str] = mapped_column(String(8))
    reference_answer: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text)
    knowledge_tags: Mapped[str] = mapped_column(Text)  # JSON 数组
    difficulty: Mapped[int] = mapped_column(Integer, default=2)
    resume_anchor: Mapped[str | None] = mapped_column(Text)  # 题目可溯源：简历原文锚点
    jd_anchor: Mapped[str | None] = mapped_column(Text)  # 题目可溯源：JD 条目锚点
    follow_up_json: Mapped[str] = mapped_column(Text, default="[]")  # 追问链（五期语音面试复用）
    quality_score: Mapped[float | None] = mapped_column()
    # embedding vector(1024) 列与 HNSW 索引在二期启用（pgvector）


class UserQuestionState(Base):
    """用户×题目的当前状态快照（掌握度/收藏/笔记）。"""

    __tablename__ = "user_question_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"))
    wrong_count: Mapped[int] = mapped_column(Integer, default=0)
    right_count: Mapped[int] = mapped_column(Integer, default=0)
    last_result: Mapped[str | None] = mapped_column(String(8))
    next_review_at: Mapped[date | None] = mapped_column(Date)
    mastered: Mapped[bool] = mapped_column(Boolean, default=False)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)


class ReviewSchedule(Base):
    """复习调度队列（艾宾浩斯到期驱动），与 user_question_state 通过 question_id 关联。"""

    __tablename__ = "review_schedule"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"), primary_key=True)
    stage: Mapped[int] = mapped_column(Integer, default=0)  # 0~4 对应 1/2/4/7/15 天
    due_date: Mapped[date] = mapped_column(Date)
    done: Mapped[bool] = mapped_column(Boolean, default=False)


class AnswerEvent(Base):
    """答题事件明细（统计事实源，生产按月分区）。"""

    __tablename__ = "answer_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"))
    set_id: Mapped[str] = mapped_column(ForeignKey("question_sets.id"))
    result: Mapped[str] = mapped_column(String(8))  # right | wrong
    mode: Mapped[str] = mapped_column(String(20), default="practice")
    answered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SiteQuestionStats(Base):
    """全站答对率（answer_events 的聚合派生表，可全量重算）。"""

    __tablename__ = "site_question_stats"

    question_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    correct_rate: Mapped[float | None] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StudyPlan(Base):
    __tablename__ = "study_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    date: Mapped[date] = mapped_column(Date, index=True)
    task_type: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(120))
    ref_id: Mapped[str | None] = mapped_column(String(64))
    est_minutes: Mapped[int] = mapped_column(Integer, default=15)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    sort: Mapped[int] = mapped_column(Integer, default=0)
