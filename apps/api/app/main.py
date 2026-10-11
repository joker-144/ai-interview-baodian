"""FastAPI 入口：挂载一期路由与 CORS。"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import (
    admin,
    auth,
    daily,
    exams,
    interview,
    jd,
    jobs,
    me,
    notifications,
    pipeline,
    plans,
    practice,
    question_sets,
    questions,
    reports,
    resumes,
    stats,
    tracking,
    wrong_book,
)

app = FastAPI(
    title=settings.app_name,
    version="0.7.0",
    description="AI 面试练习平台 · 六期已交付 + 七期 Agent 化（岗位追踪自主：订阅→定时检索→diff→匹配过滤→站内推送→下架探测；面试对话自主：STAR 评分驱动动态追问 ≤2 轮）",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(me.router)
app.include_router(plans.router)
app.include_router(reports.router)
app.include_router(exams.router)
app.include_router(question_sets.router)
app.include_router(questions.router)
app.include_router(practice.router)
app.include_router(daily.router)
app.include_router(wrong_book.router)
app.include_router(resumes.router)
app.include_router(stats.router)
app.include_router(jobs.router)
app.include_router(jd.router)
app.include_router(tracking.router)
app.include_router(pipeline.router)
app.include_router(interview.router)
app.include_router(notifications.router)
app.include_router(admin.router)


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    return {"ok": True, "version": app.version}
