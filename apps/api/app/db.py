"""MySQL 题库持久化（PyMySQL）。

建库脚本 `sql/schema.sql` 由使用者手动执行；连接参数在 `config/db.json`。

约定（保证「没配 MySQL 也能跑，配了则重启不丢数据」）：
- 启动时若库可用且已有题集记录，内存题库整体替换为库内数据（种子不再生效）；
  库为空 / 连不上 / enabled=false 时保持内存种子态；
- 写入（题目入库、新建/删除题集）为「尽力而为双写」：库操作失败只记日志
  （30 秒内最多一条，避免出题批次刷屏），内存照常工作。

注意：PyMySQL 在函数内 import —— 未安装依赖时应用仍可启动。
"""

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("uvicorn.error")

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "db.json"

DEFAULTS = {
    "enabled": True,
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "",
    "database": "ai_interview_baodian",
    "charset": "utf8mb4",
}

_lock = threading.Lock()
_conn: Any = None
_last_warn_at = 0.0


def _config() -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text("utf-8")))
    if not cfg.get("enabled", True):
        raise RuntimeError("config/db.json 已设置 enabled=false")
    return cfg


def _warn(message: str) -> None:
    """失败告警节流：库不可用时每个进程周期性提醒一次，而非每批刷屏。"""
    global _last_warn_at
    now = time.monotonic()
    if now - _last_warn_at < 30:
        return
    _last_warn_at = now
    logger.warning("题库 MySQL 持久化不可用（%s）——当前仅内存态，重启后生成数据会丢失", message)


def connection() -> Any:
    """取缓存连接；断线自动重连。失败向上抛异常，由调用方决定降级。"""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.ping(reconnect=True)
                return _conn
            except Exception:
                _conn = None
        cfg = _config()
        import pymysql  # 延迟导入：依赖缺失不影响应用启动

        _conn = pymysql.connect(
            host=cfg["host"],
            port=int(cfg["port"]),
            user=cfg["user"],
            password=cfg["password"],
            database=cfg["database"],
            charset=cfg.get("charset", "utf8mb4"),
            autocommit=True,
        )
        return _conn


def available() -> bool:
    try:
        connection()
        return True
    except Exception as exc:
        _warn(str(exc))
        return False


def _fmt(dt: datetime) -> str:
    """题库卡片展示的真实时间口径：YYYY-MM-DD HH:MM。"""
    return dt.strftime("%Y-%m-%d %H:%M")


def load_bank() -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """库可用且已有题集记录时返回 (sets, questions)；否则返回 None（调用方保持种子态）。"""
    try:
        conn = connection()
    except Exception as exc:
        _warn(str(exc))
        return None

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, source, title, question_count, updated_at "
                "FROM question_sets ORDER BY updated_at DESC"
            )
            set_rows = cur.fetchall()
            if not set_rows:
                return None
            sets = [
                {
                    "id": r[0],
                    "source": r[1],
                    "title": r[2],
                    "questionCount": r[3],
                    "updatedAt": _fmt(r[4]),
                }
                for r in set_rows
            ]

            cur.execute(
                "SELECT id, set_id, type, category, stem, options, answer, "
                "reference_answer, explanation, knowledge_tags, difficulty, site_correct_rate "
                "FROM questions"
            )
            questions = [
                {
                    "id": r[0],
                    "setId": r[1],
                    "type": r[2],
                    "category": r[3],
                    "stem": r[4],
                    "options": json.loads(r[5]),
                    "answer": r[6],
                    "referenceAnswer": r[7] or "",
                    "explanation": r[8] or "",
                    "knowledgeTags": json.loads(r[9]),
                    "difficulty": r[10],
                    "siteCorrectRate": r[11],
                }
                for r in cur.fetchall()
            ]
        return sets, questions
    except Exception as exc:
        _warn(f"读取题库失败：{exc}")
        return None


def save_set(s: dict[str, Any]) -> None:
    """题集写入（新建 / 题量与更新时间变更都走这里）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO question_sets (id, source, title, question_count) "
                "VALUES (%s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE title = VALUES(title), "
                "question_count = VALUES(question_count), updated_at = CURRENT_TIMESTAMP",
                (s["id"], s["source"], s["title"], s.get("questionCount", 0)),
            )
    except Exception as exc:
        _warn(f"题集写库失败：{exc}")


def save_questions(items: list[dict[str, Any]]) -> None:
    """题目批量写入（id 为主键，重复时幂等跳过）。"""
    if not items:
        return
    rows = [
        (
            q["id"],
            q["setId"],
            q.get("type", "single_choice"),
            q.get("category", ""),
            q["stem"],
            json.dumps(q.get("options", []), ensure_ascii=False),
            q["answer"],
            q.get("referenceAnswer") or "",
            q.get("explanation") or "",
            json.dumps(q.get("knowledgeTags", []), ensure_ascii=False),
            q.get("difficulty", 2),
            q.get("siteCorrectRate"),
        )
        for q in items
    ]
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO questions (id, set_id, type, category, stem, options, answer, "
                "reference_answer, explanation, knowledge_tags, difficulty, site_correct_rate) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE set_id = VALUES(set_id)",
                rows,
            )
    except Exception as exc:
        _warn(f"题目写库失败：{exc}")


def delete_set(set_id: str) -> None:
    """删除题集及其题目（显式双删，不依赖外键级联）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM questions WHERE set_id = %s", (set_id,))
            cur.execute("DELETE FROM question_sets WHERE id = %s", (set_id,))
    except Exception as exc:
        _warn(f"题集删库失败：{exc}")


# ---------------- 用户维度数据（资料 / 偏好 / 注销 / 进度 / 错题 / 收藏 / 简历） ----------------

_DT_FMT = "%Y-%m-%d %H:%M:%S"


def _dt_or_none(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, _DT_FMT)
    except ValueError:
        return None


def _str_or_empty(dt: datetime | None) -> str:
    return dt.strftime(_DT_FMT) if dt else ""


def _wrong_at_label(dt: datetime) -> str:
    """错题 lastWrongAt 的重启后展示口径：当天 → 今天 HH:MM，更早 → MM-DD HH:MM。"""
    now = datetime.now()
    if dt.date() == now.date():
        return f"今天 {dt.strftime('%H:%M')}"
    return dt.strftime("%m-%d %H:%M")


def load_user(user_id: str) -> dict[str, Any] | None:
    """加载单个用户的全部持久化状态；库不可用或行不存在返回 None（调用方保持内存种子态）。

    返回键：profile / settings / deactivation / progress / wrongBook / favorites /
    resumeAnalysis / resumeText / resumeSummary。展示文案（nextReviewLabel / lastWrongAt）
    在这里重算，不落库。
    """
    try:
        conn = connection()
    except Exception as exc:
        _warn(str(exc))
        return None

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, avatar_text, target_role, years, streak, total_answered, "
                "correct_rate, phone, wechat_bound, review_reminder_enabled, "
                "review_reminder_time, deactivation_status, deactivation_requested_at, "
                "deactivation_cooling_until, deactivation_reason, deactivation_scopes, "
                "deactivation_executed_at FROM users WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None

            profile = {
                "name": row[0], "avatarText": row[1], "targetRole": row[2], "years": row[3],
                "streak": row[4], "totalAnswered": row[5], "correctRate": row[6],
                "phone": row[7], "wechatBound": bool(row[8]),
            }
            settings = {
                "reviewReminderEnabled": bool(row[9]),
                "reviewReminderTime": row[10],
            }
            deactivation = None
            if row[11]:
                deactivation = {
                    "status": row[11],
                    "requestedAt": _str_or_empty(row[12]),
                    "coolingOffUntil": _str_or_empty(row[13]),
                    "reason": row[14] or "",
                    "scopes": json.loads(row[15]) if row[15] else [],
                }
                if row[16]:
                    deactivation["executedAt"] = _str_or_empty(row[16])

            cur.execute(
                "SELECT set_id, question_id, choice FROM practice_progress WHERE user_id = %s",
                (user_id,),
            )
            progress: dict[str, dict[str, str]] = {}
            for set_id, question_id, choice in cur.fetchall():
                progress.setdefault(set_id, {})[question_id] = choice

            cur.execute(
                "SELECT question_id, set_id, reason, wrong_count, stage, review_streak, "
                "mastered, last_wrong_at FROM wrong_items WHERE user_id = %s",
                (user_id,),
            )
            wrong_book: list[dict[str, Any]] = [
                {
                    "questionId": qid,
                    "setId": set_id,
                    "reason": reason,
                    "wrongCount": wrong_count,
                    "stage": stage,
                    "reviewStreak": review_streak,
                    "mastered": bool(mastered),
                    "lastWrongAt": _wrong_at_label(last_wrong_at),
                    "nextReviewLabel": _stage_label(stage),
                }
                for qid, set_id, reason, wrong_count, stage, review_streak, mastered, last_wrong_at
                in cur.fetchall()
            ]

            cur.execute("SELECT question_id FROM favorites WHERE user_id = %s", (user_id,))
            favorites = [r[0] for r in cur.fetchall()]

            cur.execute(
                "SELECT file_name, text, summary, analysis FROM resumes WHERE user_id = %s",
                (user_id,),
            )
            resume = cur.fetchone()
            resume_analysis = None
            resume_text, resume_summary = "", ""
            if resume:
                resume_analysis = json.loads(resume[3]) if resume[3] else None
                resume_text = resume[1] or ""
                resume_summary = resume[2] or ""

        return {
            "profile": profile,
            "settings": settings,
            "deactivation": deactivation,
            "progress": progress,
            "wrongBook": wrong_book,
            "favorites": favorites,
            "resumeAnalysis": resume_analysis,
            "resumeText": resume_text,
            "resumeSummary": resume_summary,
        }
    except Exception as exc:
        _warn(f"读取用户数据失败：{exc}")
        return None


def _stage_label(stage: int) -> str:
    """与 store.REVIEW_STAGE_LABELS 同构；独立定义避免 db ←→ store 循环依赖。"""
    labels = ["今天", "第 2 天", "第 4 天", "第 7 天", "第 15 天"]
    return labels[stage] if 0 <= stage < len(labels) else labels[0]


def save_user(user_id: str, profile: dict[str, Any], settings: dict[str, Any],
              deactivation: dict[str, Any] | None) -> None:
    """用户行全量 upsert（资料 + 偏好 + 注销申请一起写，行小无需拆分）。"""
    deact = deactivation or {}
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (user_id, name, avatar_text, target_role, years, streak, "
                "total_answered, correct_rate, phone, wechat_bound, review_reminder_enabled, "
                "review_reminder_time, deactivation_status, deactivation_requested_at, "
                "deactivation_cooling_until, deactivation_reason, deactivation_scopes, "
                "deactivation_executed_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE name=VALUES(name), avatar_text=VALUES(avatar_text), "
                "target_role=VALUES(target_role), years=VALUES(years), streak=VALUES(streak), "
                "total_answered=VALUES(total_answered), correct_rate=VALUES(correct_rate), "
                "phone=VALUES(phone), wechat_bound=VALUES(wechat_bound), "
                "review_reminder_enabled=VALUES(review_reminder_enabled), "
                "review_reminder_time=VALUES(review_reminder_time), "
                "deactivation_status=VALUES(deactivation_status), "
                "deactivation_requested_at=VALUES(deactivation_requested_at), "
                "deactivation_cooling_until=VALUES(deactivation_cooling_until), "
                "deactivation_reason=VALUES(deactivation_reason), "
                "deactivation_scopes=VALUES(deactivation_scopes), "
                "deactivation_executed_at=VALUES(deactivation_executed_at)",
                (
                    user_id,
                    profile.get("name", ""), profile.get("avatarText", ""),
                    profile.get("targetRole", ""), int(profile.get("years", 0)),
                    int(profile.get("streak", 0)), int(profile.get("totalAnswered", 0)),
                    int(profile.get("correctRate", 0)), profile.get("phone", ""),
                    1 if profile.get("wechatBound") else 0,
                    1 if settings.get("reviewReminderEnabled", True) else 0,
                    settings.get("reviewReminderTime", "20:00"),
                    deact.get("status"),
                    _dt_or_none(deact.get("requestedAt")),
                    _dt_or_none(deact.get("coolingOffUntil")),
                    deact.get("reason", ""),
                    json.dumps(deact.get("scopes", []), ensure_ascii=False) if deact else None,
                    _dt_or_none(deact.get("executedAt")),
                ),
            )
    except Exception as exc:
        _warn(f"用户资料写库失败：{exc}")


def save_progress(user_id: str, set_id: str, question_id: str, choice: str) -> None:
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO practice_progress (user_id, set_id, question_id, choice) "
                "VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE choice = VALUES(choice)",
                (user_id, set_id, question_id, choice),
            )
    except Exception as exc:
        _warn(f"刷题进度写库失败：{exc}")


def delete_progress(user_id: str, set_id: str) -> None:
    """清空某题集的作答记录（重置进度 / 删除题集时）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM practice_progress WHERE user_id = %s AND set_id = %s",
                (user_id, set_id),
            )
    except Exception as exc:
        _warn(f"刷题进度删库失败：{exc}")


def save_answer_event(user_id: str, set_id: str, question_id: str, choice: str,
                      is_correct: bool) -> None:
    """作答事件明细（append-only）：全站答对率统计源，每次提交一条，不随进度重置回滚。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO answer_events (user_id, set_id, question_id, choice, is_correct) "
                "VALUES (%s, %s, %s, %s, %s)",
                (user_id, set_id, question_id, choice, 1 if is_correct else 0),
            )
    except Exception as exc:
        _warn(f"作答事件写库失败：{exc}")


def load_answer_stats() -> list[dict[str, Any]]:
    """按题聚合作答事件（重启后恢复全站答对率统计）；库不可用返回空列表。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT question_id, COUNT(*), SUM(is_correct) FROM answer_events "
                "GROUP BY question_id"
            )
            return [
                {"questionId": r[0], "attempts": int(r[1]), "correct": int(r[2] or 0)}
                for r in cur.fetchall()
            ]
    except Exception as exc:
        _warn(f"作答事件聚合读取失败：{exc}")
        return []


def update_site_correct_rate(question_id: str, rate: int) -> None:
    """样本量达标时回写全站答对率（题目行不在库时静默无操作，如内存种子题）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE questions SET site_correct_rate = %s WHERE id = %s",
                (int(rate), question_id),
            )
    except Exception as exc:
        _warn(f"全站答对率回写失败：{exc}")


def save_wrong_item(user_id: str, item: dict[str, Any]) -> None:
    """错题 upsert（last_wrong_at 记真实时间；展示文案由读取时重算）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO wrong_items (user_id, question_id, set_id, reason, wrong_count, "
                "stage, review_streak, mastered, last_wrong_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP) "
                "ON DUPLICATE KEY UPDATE set_id=VALUES(set_id), reason=VALUES(reason), "
                "wrong_count=VALUES(wrong_count), stage=VALUES(stage), "
                "review_streak=VALUES(review_streak), mastered=VALUES(mastered), "
                "last_wrong_at=CURRENT_TIMESTAMP",
                (
                    user_id, item["questionId"], item["setId"], item.get("reason", "concept"),
                    int(item.get("wrongCount", 1)), int(item.get("stage", 0)),
                    int(item.get("reviewStreak", 0)), 1 if item.get("mastered") else 0,
                ),
            )
    except Exception as exc:
        _warn(f"错题写库失败：{exc}")


def delete_wrong_items(user_id: str, set_id: str) -> None:
    """删除某题集的错题（删除题集时，与内存行为一致）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM wrong_items WHERE user_id = %s AND set_id = %s",
                (user_id, set_id),
            )
    except Exception as exc:
        _warn(f"错题删库失败：{exc}")


def save_favorite(user_id: str, question_id: str) -> None:
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT IGNORE INTO favorites (user_id, question_id) VALUES (%s, %s)",
                (user_id, question_id),
            )
    except Exception as exc:
        _warn(f"收藏写库失败：{exc}")


def delete_favorite(user_id: str, question_id: str) -> None:
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM favorites WHERE user_id = %s AND question_id = %s",
                (user_id, question_id),
            )
    except Exception as exc:
        _warn(f"收藏删库失败：{exc}")


def save_resume(user_id: str, analysis: dict[str, Any] | None, text: str, summary: str) -> None:
    """最近一份简历的解析产物 upsert。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO resumes (user_id, file_name, text, summary, analysis) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE file_name=VALUES(file_name), text=VALUES(text), "
                "summary=VALUES(summary), analysis=VALUES(analysis)",
                (
                    user_id,
                    (analysis or {}).get("fileName", ""),
                    text, summary,
                    json.dumps(analysis, ensure_ascii=False) if analysis else None,
                ),
            )
    except Exception as exc:
        _warn(f"简历解析产物写库失败：{exc}")


def clear_user_data(user_id: str) -> None:
    """注销清理：删除该用户的进度 / 作答事件 / 错题 / 收藏 / 简历产物（users 行保留注销标记）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            for sql in (
                "DELETE FROM practice_progress WHERE user_id = %s",
                "DELETE FROM answer_events WHERE user_id = %s",
                "DELETE FROM wrong_items WHERE user_id = %s",
                "DELETE FROM favorites WHERE user_id = %s",
                "DELETE FROM resumes WHERE user_id = %s",
            ):
                cur.execute(sql, (user_id,))
    except Exception as exc:
        _warn(f"注销清理失败：{exc}")


def clear_bank() -> None:
    """注销清理：清空全站题库（题集级联删除题目），与内存清 sets 行为对齐。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM question_sets")
    except Exception as exc:
        _warn(f"题库清理失败：{exc}")


# ---------------- 账号体系：注册 / 登录 / 演示账号 ----------------


def create_user(user_id: str, account: str, password_hash: str, profile: dict[str, Any],
                settings: dict[str, Any], deactivation: dict[str, Any] | None) -> None:
    """注册建号：写入账号列（唯一索引兜底重复注册）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (user_id, account, password_hash, name, avatar_text, "
                "target_role, years, streak, total_answered, correct_rate, phone, wechat_bound, "
                "review_reminder_enabled, review_reminder_time, deactivation_status, "
                "deactivation_requested_at, deactivation_cooling_until, deactivation_reason, "
                "deactivation_scopes, deactivation_executed_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    user_id, account, password_hash,
                    profile.get("name", ""), profile.get("avatarText", ""),
                    profile.get("targetRole", ""), int(profile.get("years", 0)),
                    int(profile.get("streak", 0)), int(profile.get("totalAnswered", 0)),
                    int(profile.get("correctRate", 0)), profile.get("phone", ""),
                    1 if profile.get("wechatBound") else 0,
                    1 if settings.get("reviewReminderEnabled", True) else 0,
                    settings.get("reviewReminderTime", "20:00"),
                    deact["status"] if (deact := deactivation or {}) else None,
                    _dt_or_none(deact.get("requestedAt")),
                    _dt_or_none(deact.get("coolingOffUntil")),
                    deact.get("reason", ""),
                    json.dumps(deact.get("scopes", []), ensure_ascii=False) if deact else None,
                    _dt_or_none(deact.get("executedAt")),
                ),
            )
    except Exception as exc:
        _warn(f"注册建号写库失败：{exc}")


def get_user_by_account(account: str) -> dict[str, Any] | None:
    """登录查号：按账号（唯一索引）取 user_id 与密码哈希；库不可用返回 None。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, password_hash FROM users WHERE account = %s", (account,)
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {"userId": row[0], "passwordHash": row[1] or ""}
    except Exception as exc:
        _warn(f"登录查号失败：{exc}")
        return None


def seed_demo_account(user_id: str, account: str, password_hash: str,
                      profile: dict[str, Any]) -> None:
    """幂等种子演示账号：首次启动写入，已有该 user_id 则跳过（INSERT IGNORE 走主键）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT IGNORE INTO users (user_id, account, password_hash, name, avatar_text, "
                "target_role, years, phone) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    user_id, account, password_hash,
                    profile.get("name", ""), profile.get("avatarText", ""),
                    profile.get("targetRole", ""), int(profile.get("years", 0)),
                    profile.get("phone", ""),
                ),
            )
    except Exception as exc:
        _warn(f"演示账号种子写入失败：{exc}")


# ---------------- 出题任务（中断恢复） ----------------


def create_generate_task(task_id: str, user_id: str, set_id: str, total: int) -> None:
    """任务创建时插入 running 行；后续每批更新 generated_cnt / dropped_cnt。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO generate_tasks (task_id, user_id, set_id, total) VALUES (%s,%s,%s,%s)",
                (task_id, user_id, set_id, int(total)),
            )
    except Exception as exc:
        _warn(f"出题任务写库失败：{exc}")


def update_generate_progress(task_id: str, generated: int, dropped: int) -> None:
    """每批生成结束后更新进度（低频：80 题约 20 次写库）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE generate_tasks SET generated_cnt = %s, dropped_cnt = %s WHERE task_id = %s",
                (int(generated), int(dropped), task_id),
            )
    except Exception as exc:
        _warn(f"出题进度写库失败：{exc}")


def finish_generate_task(task_id: str, error: str | None) -> None:
    """任务结束写终态（done）；error 为本次生成失败原因，成功时为 NULL。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE generate_tasks SET status = 'done', error = %s, finished_at = NOW() "
                "WHERE task_id = %s",
                (error, task_id),
            )
    except Exception as exc:
        _warn(f"出题任务终态写库失败：{exc}")


def load_interrupted_generate_tasks() -> list[dict[str, Any]]:
    """启动恢复：取全部 running 行（进程重启后即为中断任务），并标记为 interrupted。

    库不可用时返回空列表（调用方保持无恢复态）。
    """
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE generate_tasks SET status = 'interrupted', finished_at = NOW() "
                "WHERE status = 'running'"
            )
            cur.execute(
                "SELECT task_id, user_id, set_id, total, generated_cnt, dropped_cnt, error "
                "FROM generate_tasks WHERE status = 'interrupted'"
            )
            return [
                {
                    "taskId": row[0], "userId": row[1], "setId": row[2],
                    "total": int(row[3]), "generated": int(row[4]), "dropped": int(row[5]),
                }
                for row in cur.fetchall()
            ]
    except Exception as exc:
        _warn(f"出题任务恢复加载失败：{exc}")
        return []


def dismiss_interrupted_tasks(user_id: str) -> None:
    """续作 / 重新生成发起时归档该用户旧的中断任务（不再进入恢复查询）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE generate_tasks SET status = 'done', error = NULL "
                "WHERE user_id = %s AND status = 'interrupted'",
                (user_id,),
            )
    except Exception as exc:
        _warn(f"中断任务归档失败：{exc}")
