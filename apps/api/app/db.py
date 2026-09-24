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
