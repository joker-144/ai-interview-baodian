"""通知中心（二期批 4，产品文档 4.2）。

触发源（无定时器口径，全部惰性/事件驱动）：
- generate_done：出题任务完成（app/generation.py 终态处挂接）；
- exam_report：模考交卷判分完成（app/routers/exams.py submit 处挂接）；
- review_due：GET 本接口时惰性检查——当日到期错题（未掌握且 stage=0）> 0
  且今日未写过该汇总时补写一条（当日去重，不重复轰炸）。
- interview_prep（四期）：求职看板 GET /api/pipeline 时惰性检查——未来 ≤3 天有面试
  且当日未写过时补写一条（当日去重，见 routers/pipeline.py `_lazy_interview_prep`）。

Web 期口径：站内信 + 前端浏览器通知（Notification API，前端每 60s 轮询），
无推送服务。顶栏不加导航项，通知入口在 /me 内（8.3 约束）。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from app import store
from app.auth import get_current_user

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _lazy_review_due(user_id: str) -> None:
    """复习到期汇总：当日到期错题 > 0 且今日未写过时补写一条（当日只写一次）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    if store.find_notification_by_date(user_id, "review_due", today):
        return
    due = sum(
        1
        for item in store.state.wrong_book.get(user_id, [])
        if not item.get("mastered") and int(item.get("stage", 0)) == 0
    )
    if due > 0:
        store.add_notification(user_id, "review_due", {"count": due})


@router.get("")
def list_notifications(user_id: str = Depends(get_current_user)) -> dict:
    """倒序列表 + 未读数；顺带惰性补写当日复习到期汇总。"""
    store.ensure_user(user_id)
    _lazy_review_due(user_id)
    items = store.user_notifications(user_id)
    return {
        "unread": sum(1 for n in items if not n["read"]),
        "items": [
            {
                "id": n["id"],
                "type": n["type"],
                "payload": n["payload"],
                "read": n["read"],
                "createdAt": n["createdAt"],
            }
            for n in items
        ],
    }


@router.put("/read-all")
def read_all(user_id: str = Depends(get_current_user)) -> dict:
    """全部已读（声明在 /{notification_id} 之前，避免被路径参数吞掉）。"""
    store.ensure_user(user_id)
    return {"updated": store.mark_all_notifications_read(user_id)}


@router.put("/{notification_id}/read")
def read_one(
    notification_id: str, user_id: str = Depends(get_current_user)
) -> dict:
    """单条已读。"""
    store.ensure_user(user_id)
    if not store.mark_notification_read(user_id, notification_id):
        raise HTTPException(status_code=404, detail="通知不存在或已读")
    return {"ok": True}
