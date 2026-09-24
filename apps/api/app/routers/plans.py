"""今日学习计划：列表 / 勾选切换 / 添加。"""

from fastapi import APIRouter, HTTPException

from app import store
from app.schemas import PlanAddRequest, PlanTask

router = APIRouter(prefix="/api/plans", tags=["plans"])


@router.get("", response_model=list[PlanTask])
def list_plans() -> list[dict]:
    return store.state.plans


@router.post("", response_model=PlanTask, status_code=201)
def add_plan(body: PlanAddRequest) -> dict:
    task = {
        "id": store.new_id("plan"),
        "type": "practice",
        "title": body.title,
        "estMinutes": 15,
        "done": False,
    }
    store.state.plans.append(task)
    return task


@router.post("/{plan_id}/toggle", response_model=PlanTask)
def toggle_plan(plan_id: str) -> dict:
    for task in store.state.plans:
        if task["id"] == plan_id:
            task["done"] = not task["done"]
            return task
    raise HTTPException(status_code=404, detail="计划不存在")
