"""FastAPI 入口：挂载一期路由与 CORS。"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import (
    admin,
    auth,
    me,
    plans,
    practice,
    question_sets,
    questions,
    resumes,
    stats,
    wrong_book,
)

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="AI 面试练习平台 · 一期接口（内存 Mock 实现）",
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
app.include_router(question_sets.router)
app.include_router(questions.router)
app.include_router(practice.router)
app.include_router(wrong_book.router)
app.include_router(resumes.router)
app.include_router(stats.router)
app.include_router(admin.router)


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    return {"ok": True, "version": app.version}
