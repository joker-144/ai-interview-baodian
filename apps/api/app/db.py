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

# 连接按线程隔离：FastAPI 同步端点跑在线程池、后台出题任务跑在事件循环线程，
# pymysql 连接非线程安全——多线程共用同一连接会在并发请求下损坏协议流
# （随机报 "read of closed file" 等读库失败，进而误降级内存态、看板被清空）。
_local = threading.local()
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
    """取本线程缓存连接；断线自动重连。失败向上抛异常，由调用方决定降级。

    线程隔离（threading.local）：线程池上限有限（anyio 默认 40），连接数可控；
    结构升级 _ensure_upgrade 自带进程级只跑一次的守卫，不会逐线程重跑。
    """
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.ping(reconnect=True)
            return conn
        except Exception:
            _local.conn = None
    cfg = _config()
    import pymysql  # 延迟导入：依赖缺失不影响应用启动

    conn = pymysql.connect(
        host=cfg["host"],
        port=int(cfg["port"]),
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        charset=cfg.get("charset", "utf8mb4"),
        autocommit=True,
    )
    _ensure_upgrade(conn)
    _local.conn = conn
    return conn


def available() -> bool:
    try:
        connection()
        return True
    except Exception as exc:
        _warn(str(exc))
        return False


# 结构自动升级只跑一次（进程级）；schema.sql 始终为最新全量，旧库免重跑
_upgrade_done = False


def _ensure_upgrade(conn: Any) -> None:
    """旧库幂等升级：补建二期新表 / 新列（MySQL 无 ADD COLUMN IF NOT EXISTS，逐条容忍已存在）。"""
    global _upgrade_done
    if _upgrade_done:
        return
    _upgrade_done = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS study_plans ("
                "  id             VARCHAR(64)  NOT NULL COMMENT '计划任务 id',"
                "  user_id        VARCHAR(64)  NOT NULL COMMENT '所属用户（JWT sub）',"
                "  plan_date      DATE         NOT NULL COMMENT '计划日期',"
                "  type           VARCHAR(16)  NOT NULL DEFAULT 'practice' COMMENT '任务类型',"
                "  title          VARCHAR(255) NOT NULL COMMENT '任务标题',"
                "  est_minutes    INT          NOT NULL DEFAULT 15 COMMENT '时长预估（分钟）',"
                "  done           TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否完成',"
                "  auto_generated TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否 AI 生成',"
                "  ref_id         VARCHAR(64)  NULL COMMENT '关联资源 id',"
                "  created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "  PRIMARY KEY (id),"
                "  KEY idx_plans_user_date (user_id, plan_date)"
                ") ENGINE = InnoDB DEFAULT CHARSET = utf8mb4"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS exam_records ("
                "  id              VARCHAR(64)  NOT NULL COMMENT '考试 id',"
                "  user_id         VARCHAR(64)  NOT NULL COMMENT '所属用户（JWT sub）',"
                "  set_id          VARCHAR(64)  NOT NULL COMMENT '出卷题集 id',"
                "  bucket_role     VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '岗位分桶',"
                "  total           INT          NOT NULL DEFAULT 0 COMMENT '卷面题量',"
                "  question_ids    JSON         NOT NULL COMMENT '组卷快照',"
                "  answered_detail JSON         NULL COMMENT '已答明细（逐题覆盖写）',"
                "  score           INT          NULL COMMENT '百分制得分',"
                "  duration_sec    INT          NOT NULL DEFAULT 0 COMMENT '有效作答用时',"
                "  paused_sec      INT          NOT NULL DEFAULT 0 COMMENT '累计暂停秒数',"
                "  status          VARCHAR(16)  NOT NULL DEFAULT 'running' COMMENT 'running / paused / done / abandoned',"
                "  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '开考时间',"
                "  finished_at     DATETIME     NULL COMMENT '交卷时间',"
                "  PRIMARY KEY (id),"
                "  KEY idx_exams_user (user_id, status),"
                "  KEY idx_exams_role (bucket_role, status)"
                ") ENGINE = InnoDB DEFAULT CHARSET = utf8mb4"
            )
            try:
                cur.execute(
                    "ALTER TABLE users ADD COLUMN last_checkin_date DATE NULL "
                    "COMMENT '最近一次学习计划打卡达成日' AFTER deactivation_executed_at"
                )
            except Exception:
                pass  # 列已存在
            try:
                # 旧库 job_details.security_id 为 VARCHAR(64)，而 BOSS 真实 security_id 可长达 ~340 字符，
                # 写库会报 (1406, Data too long)；幂等拓宽到 VARCHAR(512)（utf8mb4 下 PK 前缀 2048B < 3072B 上限）。
                cur.execute(
                    "ALTER TABLE job_details MODIFY security_id VARCHAR(512) NOT NULL "
                    "COMMENT 'BOSS 岗位 security_id（实测可长达 ~340 字符）'"
                )
            except Exception:
                pass  # 表不存在或已是目标宽度
            cur.execute(
                "CREATE TABLE IF NOT EXISTS notifications ("
                "  id           VARCHAR(64)  NOT NULL COMMENT '通知 id',"
                "  user_id      VARCHAR(64)  NOT NULL COMMENT '所属用户（JWT sub）',"
                "  type         VARCHAR(32)  NOT NULL COMMENT 'generate_done / exam_report / review_due',"
                "  payload_json JSON         NULL COMMENT '通知负载',"
                "  `read`       TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否已读',"
                "  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "  PRIMARY KEY (id),"
                "  KEY idx_notifications_user (user_id, `read`)"
                ") ENGINE = InnoDB DEFAULT CHARSET = utf8mb4"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS jobs_cache ("
                "  cache_key    VARCHAR(64)  NOT NULL COMMENT '缓存 key（sha256(关键词|城市)）',"
                "  keyword      VARCHAR(128) NOT NULL COMMENT '检索关键词',"
                "  city         VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '城市',"
                "  payload_json JSON         NOT NULL COMMENT '采样后的岗位卡列表',"
                "  expires_at   DATETIME     NOT NULL COMMENT '过期时间',"
                "  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "  PRIMARY KEY (cache_key),"
                "  KEY idx_jobs_cache_exp (expires_at)"
                ") ENGINE = InnoDB DEFAULT CHARSET = utf8mb4"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS job_details ("
                "  security_id  VARCHAR(512) NOT NULL COMMENT 'BOSS 岗位 security_id（实测可长达 ~340 字符）',"
                "  keyword      VARCHAR(128) NOT NULL DEFAULT '' COMMENT '来源检索关键词',"
                "  payload_json JSON         NOT NULL COMMENT 'JD 全量详情',"
                "  expires_at   DATETIME     NOT NULL COMMENT '过期时间',"
                "  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "  PRIMARY KEY (security_id),"
                "  KEY idx_job_details_exp (expires_at)"
                ") ENGINE = InnoDB DEFAULT CHARSET = utf8mb4"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS job_maps ("
                "  map_key      VARCHAR(64)  NOT NULL COMMENT '缓存 key（sha256(关键词|城市)）',"
                "  keyword      VARCHAR(128) NOT NULL COMMENT '检索关键词',"
                "  city         VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '城市',"
                "  payload_json JSON         NOT NULL COMMENT '聚合报告（topSkills/duties/salaryInsight/tiers）',"
                "  expires_at   DATETIME     NOT NULL COMMENT '过期时间',"
                "  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "  PRIMARY KEY (map_key),"
                "  KEY idx_job_maps_exp (expires_at)"
                ") ENGINE = InnoDB DEFAULT CHARSET = utf8mb4"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS daily_practices ("
                "  id             VARCHAR(64)  NOT NULL COMMENT '每日一练 id',"
                "  user_id        VARCHAR(64)  NOT NULL COMMENT '所属用户（JWT sub）',"
                "  practice_date  DATE         NOT NULL COMMENT '练习日期',"
                "  question_ids   JSON         NOT NULL COMMENT '当日 10 题快照',"
                "  done_ids       JSON         NULL COMMENT '已完成题目 id 数组',"
                "  created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "  PRIMARY KEY (id),"
                "  UNIQUE KEY uk_daily_user_date (user_id, practice_date)"
                ") ENGINE = InnoDB DEFAULT CHARSET = utf8mb4"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS job_pipeline ("
                "  id            VARCHAR(64)  NOT NULL COMMENT '看板卡 id',"
                "  user_id       VARCHAR(64)  NOT NULL COMMENT '所属用户（JWT sub）',"
                "  stage         VARCHAR(16)  NOT NULL DEFAULT 'applied',"
                "  security_id   VARCHAR(512) NOT NULL DEFAULT '',"
                "  keyword       VARCHAR(128) NOT NULL DEFAULT '',"
                "  job_name      VARCHAR(255) NOT NULL DEFAULT '',"
                "  brand         VARCHAR(255) NOT NULL DEFAULT '',"
                "  city          VARCHAR(64)  NOT NULL DEFAULT '',"
                "  salary        VARCHAR(64)  NOT NULL DEFAULT '',"
                "  platform      VARCHAR(16)  NOT NULL DEFAULT 'zhipin',"
                "  match_score   INT          NULL,"
                "  set_id        VARCHAR(64)  NOT NULL DEFAULT '',"
                "  interview_at  DATE         NULL,"
                "  note          TEXT         NULL,"
                "  in_trash      TINYINT      NOT NULL DEFAULT 0,"
                "  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "  updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
                "  PRIMARY KEY (id),"
                "  KEY idx_pipeline_user_stage (user_id, stage),"
                "  KEY idx_pipeline_user_trash (user_id, in_trash)"
                ") ENGINE = InnoDB DEFAULT CHARSET = utf8mb4"
            )
            # 题库归属隔离：旧库补 owner_id 列（空=公共题库/预置种子），幂等容忍已存在
            try:
                cur.execute(
                    "ALTER TABLE question_sets ADD COLUMN owner_id VARCHAR(64) NOT NULL DEFAULT '' "
                    "COMMENT '归属用户 user_id（空=公共题库/预置种子）' AFTER source"
                )
                cur.execute(
                    "ALTER TABLE question_sets ADD KEY idx_question_sets_owner (owner_id)"
                )
            except Exception:
                pass  # 列/索引已存在
            try:
                # 历史引擎生成题集按出题任务回填真实归属者（set_id -> user_id）
                cur.execute(
                    "UPDATE question_sets qs JOIN ("
                    "  SELECT set_id, MIN(user_id) AS uid FROM generate_tasks GROUP BY set_id"
                    ") gt ON gt.set_id = qs.id SET qs.owner_id = gt.uid WHERE qs.owner_id = ''"
                )
                # 无任务关联的历史题集（手动自建）兜底归属 smoke_db_user；库中不含预置种子，安全
                cur.execute(
                    "UPDATE question_sets SET owner_id = ("
                    "  SELECT user_id FROM users WHERE account = 'smoke_db_user' LIMIT 1) "
                    "WHERE owner_id = '' "
                    "AND EXISTS (SELECT 1 FROM users WHERE account = 'smoke_db_user')"
                )
            except Exception as exc:
                _warn(f"question_sets.owner_id 回填失败（不影响隔离逻辑，新题集仍按用户归属）：{exc}")
            # 演示账号种子曾写入伪造进度（累计作答 1024 / 答对率 78 / 连续 12 天）；
            # 进度统一改为真实作答事件驱动，将未被真实使用过的 demo 底数归零（幂等，不伤真实数据）
            try:
                cur.execute(
                    "UPDATE users SET streak = 0, total_answered = 0, correct_rate = 0 "
                    "WHERE user_id = 'demo-user' AND total_answered = 1024 "
                    "AND correct_rate = 78 AND streak = 12"
                )
            except Exception as exc:
                _warn(f"demo 演示进度归零失败（不影响真实统计）：{exc}")
            # resumes 旧结构（user_id 主键单行）自动迁移为多版本表；失败不阻塞主链路
            try:
                cur.execute("SELECT version FROM resumes LIMIT 1")
                cur.fetchall()
            except Exception:
                try:
                    cur.execute("RENAME TABLE resumes TO resumes_legacy")
                    cur.execute(
                        "CREATE TABLE resumes ("
                        "  resume_id    VARCHAR(64)  NOT NULL COMMENT '简历版本 id',"
                        "  user_id      VARCHAR(64)  NOT NULL COMMENT '所属用户（JWT sub）',"
                        "  version      INT          NOT NULL DEFAULT 1 COMMENT '版本号',"
                        "  is_optimized TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否 AI 优化版',"
                        "  file_name    VARCHAR(255) NOT NULL DEFAULT '' COMMENT '上传文件名',"
                        "  text         MEDIUMTEXT   NULL COMMENT '简历原文',"
                        "  summary      MEDIUMTEXT   NULL COMMENT 'AI 画像摘要',"
                        "  analysis     JSON         NULL COMMENT '结构化解析结果（含体检）',"
                        "  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',"
                        "  PRIMARY KEY (resume_id),"
                        "  KEY idx_resumes_user (user_id)"
                        ") ENGINE = InnoDB DEFAULT CHARSET = utf8mb4"
                    )
                    cur.execute(
                        "INSERT INTO resumes (resume_id, user_id, version, is_optimized, "
                        "file_name, text, summary, analysis, created_at) "
                        "SELECT CONCAT(user_id, '-v1'), user_id, 1, 0, file_name, text, "
                        "summary, analysis, updated_at FROM resumes_legacy"
                    )
                    cur.execute("DROP TABLE resumes_legacy")
                    conn.commit()
                    logger.warning("resumes 表已自动迁移为多版本结构（旧单行 -> version=1）")
                except Exception as exc:
                    _warn(
                        f"resumes 表旧结构自动迁移失败（不影响内存态运行），"
                        f"请按 sql/schema.sql 头部说明手工迁移：{exc}"
                    )
    except Exception as exc:
        _warn(f"库结构自动升级失败（不影响内存态运行）：{exc}")


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
                "SELECT id, source, title, question_count, updated_at, owner_id "
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
                    "ownerId": r[5] or "",
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
                "INSERT INTO question_sets (id, source, title, question_count, owner_id) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE title = VALUES(title), "
                "question_count = VALUES(question_count), updated_at = CURRENT_TIMESTAMP",
                (s["id"], s["source"], s["title"], s.get("questionCount", 0), s.get("ownerId", "")),
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


def _date_or_none(value: str | None) -> datetime | None:
    """lastCheckinDate（YYYY-MM-DD）→ DATE；空串 / 格式错返回 NULL。"""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


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
                "deactivation_executed_at, last_checkin_date FROM users WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None

            profile = {
                "name": row[0], "avatarText": row[1], "targetRole": row[2], "years": row[3],
                "streak": row[4], "totalAnswered": row[5], "correctRate": row[6],
                "phone": row[7], "wechatBound": bool(row[8]),
                # 打卡日期不外发，仅用于服务端判断「同日不重复计 streak」
                "lastCheckinDate": row[17].strftime("%Y-%m-%d") if row[17] else "",
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
                "SELECT file_name, text, summary, analysis FROM resumes "
                "WHERE user_id = %s ORDER BY version DESC, created_at DESC LIMIT 1",
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
                "deactivation_executed_at, last_checkin_date) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
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
                "deactivation_executed_at=VALUES(deactivation_executed_at), "
                "last_checkin_date=VALUES(last_checkin_date)",
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
                    _date_or_none(profile.get("lastCheckinDate")),
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


# ---------------- 今日学习计划（二期） ----------------


def load_plans(user_id: str, plan_date: str) -> list[dict[str, Any]]:
    """取某用户某天的计划任务（按创建顺序）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, type, title, est_minutes, done, auto_generated, ref_id "
                "FROM study_plans WHERE user_id = %s AND plan_date = %s ORDER BY created_at, id",
                (user_id, plan_date),
            )
            return [
                {
                    "id": r[0], "type": r[1], "title": r[2], "estMinutes": int(r[3]),
                    "done": bool(r[4]), "autoGenerated": bool(r[5]), "refId": r[6],
                }
                for r in cur.fetchall()
            ]
    except Exception as exc:
        _warn(f"学习计划读取失败：{exc}")
        return []


def save_plan(user_id: str, plan_date: str, item: dict[str, Any]) -> None:
    """计划任务 upsert（id 主键；勾选切换 / 生成 / 添加都走这里）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO study_plans (id, user_id, plan_date, type, title, est_minutes, "
                "done, auto_generated, ref_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE type=VALUES(type), title=VALUES(title), "
                "est_minutes=VALUES(est_minutes), done=VALUES(done), "
                "auto_generated=VALUES(auto_generated), ref_id=VALUES(ref_id)",
                (
                    item["id"], user_id, plan_date, item.get("type", "practice"),
                    item["title"], int(item.get("estMinutes", 15)),
                    1 if item.get("done") else 0,
                    1 if item.get("autoGenerated") else 0,
                    item.get("refId"),
                ),
            )
    except Exception as exc:
        _warn(f"学习计划写库失败：{exc}")


def delete_plan(user_id: str, plan_id: str) -> None:
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM study_plans WHERE user_id = %s AND id = %s",
                (user_id, plan_id),
            )
    except Exception as exc:
        _warn(f"学习计划删库失败：{exc}")


def load_week_events(user_id: str, days: int = 7) -> list[dict[str, Any]]:
    """近 N 天逐日作答聚合（周报 / 本周图表源）；库不可用返回空列表。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DATE(created_at), COUNT(*), SUM(is_correct) FROM answer_events "
                "WHERE user_id = %s AND created_at >= CURDATE() - INTERVAL %s DAY "
                "GROUP BY DATE(created_at) ORDER BY DATE(created_at)",
                (user_id, int(days) - 1),
            )
            return [
                {"date": str(r[0]), "answered": int(r[1]), "correct": int(r[2] or 0)}
                for r in cur.fetchall()
            ]
    except Exception as exc:
        _warn(f"周作答聚合读取失败：{exc}")
        return []


def load_user_answer_totals(user_id: str) -> dict[str, int] | None:
    """累计作答口径（全部时间）：从 answer_events 聚合 {answered, correct}；库不可用返回 None。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*), SUM(is_correct) FROM answer_events WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            return {"answered": int(row[0] or 0), "correct": int(row[1] or 0)}
    except Exception as exc:
        _warn(f"累计作答聚合读取失败：{exc}")
        return None


# ---------------- 模拟考试（二期） ----------------


def save_exam_record(exam: dict[str, Any]) -> None:
    """考试记录 upsert 全行：作答/暂停/交卷每次变更都覆盖写（增量口径由调用方保证）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO exam_records (id, user_id, set_id, bucket_role, total, "
                "question_ids, answered_detail, score, duration_sec, paused_sec, status, "
                "created_at, finished_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE answered_detail=VALUES(answered_detail), "
                "score=VALUES(score), duration_sec=VALUES(duration_sec), "
                "paused_sec=VALUES(paused_sec), status=VALUES(status), "
                "finished_at=VALUES(finished_at)",
                (
                    exam["id"], exam["userId"], exam["setId"], exam.get("bucketRole", ""),
                    int(exam.get("total", 0)),
                    json.dumps(exam.get("questionIds", []), ensure_ascii=False),
                    json.dumps(exam.get("answers", []), ensure_ascii=False) or None,
                    exam.get("score"), int(exam.get("durationSec", 0)),
                    int(exam.get("pausedSec", 0)), exam.get("status", "running"),
                    exam.get("createdAt") or datetime.now(),
                    exam.get("finishedAt"),
                ),
            )
            conn.commit()
    except Exception as exc:
        _warn(f"考试记录写库失败：{exc}")


def load_exam(user_id: str, exam_id: str) -> dict[str, Any] | None:
    """取一条考试记录（属主校验由调用方做；库不可用返回 None）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id, set_id, bucket_role, total, question_ids, "
                "answered_detail, score, duration_sec, paused_sec, status, created_at, "
                "finished_at FROM exam_records WHERE id = %s AND user_id = %s",
                (exam_id, user_id),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return _exam_row(row)
    except Exception as exc:
        _warn(f"考试记录读取失败：{exc}")
        return None


def load_exams(user_id: str) -> list[dict[str, Any]]:
    """该用户全部考试记录（倒序，历史列表与趋势源）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id, set_id, bucket_role, total, question_ids, "
                "answered_detail, score, duration_sec, paused_sec, status, created_at, "
                "finished_at FROM exam_records WHERE user_id = %s "
                "ORDER BY created_at DESC, id DESC",
                (user_id,),
            )
            return [_exam_row(r) for r in cur.fetchall()]
    except Exception as exc:
        _warn(f"考试列表读取失败：{exc}")
        return []


def load_done_scores(bucket_role: str) -> list[int]:
    """同岗位分桶的已交卷分数（升序，百分位计算源）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT score FROM exam_records "
                "WHERE bucket_role = %s AND status = 'done' AND score IS NOT NULL "
                "ORDER BY score",
                (bucket_role,),
            )
            return [int(r[0]) for r in cur.fetchall()]
    except Exception as exc:
        _warn(f"分桶分数读取失败：{exc}")
        return []


def _exam_row(row: tuple) -> dict[str, Any]:
    """exam_records 行 -> 内存 exam dict（JSON 列已由 PyMySQL 反序列化）。"""
    return {
        "id": row[0], "userId": row[1], "setId": row[2], "bucketRole": row[3] or "",
        "total": int(row[4]),
        "questionIds": row[5] if isinstance(row[5], list) else json.loads(row[5] or "[]"),
        "answers": row[6] if isinstance(row[6], list) else json.loads(row[6] or "[]"),
        "score": int(row[7]) if row[7] is not None else None,
        "durationSec": int(row[8]), "pausedSec": int(row[9]),
        "status": row[10],
        "createdAt": row[11].strftime("%Y-%m-%d %H:%M:%S") if row[11] else "",
        "finishedAt": row[12].strftime("%Y-%m-%d %H:%M:%S") if row[12] else None,
    }


def _resume_row(row: tuple) -> dict[str, Any]:
    """resumes v2 行 -> 前端同构 camelCase 记录。"""
    (resume_id, version, is_optimized, file_name, text, summary, analysis, created_at) = row
    return {
        "resumeId": resume_id,
        "version": int(version or 1),
        "isOptimized": bool(is_optimized),
        "fileName": file_name or "",
        "text": text or "",
        "summary": summary or "",
        "analysis": json.loads(analysis) if analysis else None,
        "createdAt": str(created_at) if created_at else "",
    }


_RESUME_COLS = (
    "resume_id, version, is_optimized, file_name, text, summary, analysis, created_at"
)


def load_resumes(user_id: str) -> list[dict[str, Any]]:
    """该用户全部简历版本（version 降序；含体检等完整 analysis）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_RESUME_COLS} FROM resumes WHERE user_id = %s "
                "ORDER BY version DESC, created_at DESC",
                (user_id,),
            )
            return [_resume_row(r) for r in cur.fetchall()]
    except Exception as exc:
        _warn(f"读取简历版本列表失败：{exc}")
        return []


def load_resume(user_id: str, resume_id: str) -> dict[str, Any] | None:
    """按 id 读一个简历版本。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_RESUME_COLS} FROM resumes "
                "WHERE user_id = %s AND resume_id = %s",
                (user_id, resume_id),
            )
            row = cur.fetchone()
    except Exception as exc:
        _warn(f"读取简历版本失败：{exc}")
        return None
    return _resume_row(row) if row else None


def save_resume_record(rec: dict[str, Any]) -> None:
    """简历版本行 upsert（新增版本 / 体检惰性补算写回 analysis 都走这里）。"""
    analysis = rec.get("analysis")
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO resumes (resume_id, user_id, version, is_optimized, "
                "file_name, text, summary, analysis) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE is_optimized=VALUES(is_optimized), "
                "file_name=VALUES(file_name), text=VALUES(text), "
                "summary=VALUES(summary), analysis=VALUES(analysis)",
                (
                    rec["resumeId"], rec["userId"], int(rec.get("version", 1)),
                    1 if rec.get("isOptimized") else 0,
                    rec.get("fileName", ""), rec.get("text", ""),
                    rec.get("summary", ""),
                    json.dumps(analysis, ensure_ascii=False) if analysis is not None else None,
                ),
            )
    except Exception as exc:
        _warn(f"简历版本写库失败：{exc}")


def delete_resume(user_id: str, resume_id: str) -> None:
    """删除该用户一个简历版本。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM resumes WHERE user_id = %s AND resume_id = %s",
                (user_id, resume_id),
            )
    except Exception as exc:
        _warn(f"简历版本删库失败：{exc}")


# ---------------- 站内通知（批 4） ----------------


def save_notification(rec: dict[str, Any]) -> None:
    """站内信写入（append-only）。"""
    payload = rec.get("payload")
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO notifications (id, user_id, type, payload_json, `read`) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    rec["id"], rec["userId"], rec["type"],
                    json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                    1 if rec.get("read") else 0,
                ),
            )
    except Exception as exc:
        _warn(f"站内信写库失败：{exc}")


def load_notifications(user_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """站内信倒序列表（created_at 降序；读接口同构 camelCase）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, type, payload_json, `read`, created_at FROM notifications "
                "WHERE user_id = %s ORDER BY created_at DESC, id DESC LIMIT %s",
                (user_id, int(limit)),
            )
            rows = cur.fetchall()
    except Exception as exc:
        _warn(f"站内信读库失败：{exc}")
        return []
    return [
        {
            "id": nid,
            "type": ntype,
            "payload": json.loads(payload) if payload else None,
            "read": bool(is_read),
            "createdAt": str(created_at),
        }
        for nid, ntype, payload, is_read, created_at in rows
    ]


def mark_notification_read(user_id: str, notification_id: str) -> bool:
    """单条已读；返回是否确实更新到行。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            count = cur.execute(
                "UPDATE notifications SET `read` = 1 "
                "WHERE user_id = %s AND id = %s AND `read` = 0",
                (user_id, notification_id),
            )
        return bool(count)
    except Exception as exc:
        _warn(f"站内信已读写库失败：{exc}")
        return False


def mark_all_notifications_read(user_id: str) -> int:
    """全部已读；返回更新的行数（库不可用返回 -1，由内存态兜底）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            count = cur.execute(
                "UPDATE notifications SET `read` = 1 "
                "WHERE user_id = %s AND `read` = 0",
                (user_id,),
            )
        return int(count)
    except Exception as exc:
        _warn(f"站内信全部已读写库失败：{exc}")
        return -1


def clear_user_data(user_id: str) -> None:
    """注销清理：删除该用户全部数据行（进度 / 作答事件 / 错题 / 收藏 / 简历 / 计划 /
    模考 / 站内信 / 每日一练 / 求职看板），users 行保留注销标记。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            for sql in (
                "DELETE FROM practice_progress WHERE user_id = %s",
                "DELETE FROM answer_events WHERE user_id = %s",
                "DELETE FROM wrong_items WHERE user_id = %s",
                "DELETE FROM favorites WHERE user_id = %s",
                "DELETE FROM resumes WHERE user_id = %s",
                "DELETE FROM study_plans WHERE user_id = %s",
                "DELETE FROM exam_records WHERE user_id = %s",
                "DELETE FROM notifications WHERE user_id = %s",
                "DELETE FROM daily_practices WHERE user_id = %s",
                "DELETE FROM job_pipeline WHERE user_id = %s",
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


# ---------------- 岗位检索缓存（三期，TTL 惰性刷新；天数由 store.JOBS_CACHE_TTL_DAYS 决定） ----------------


def save_jobs_cache(cache_key: str, keyword: str, city: str, payload: Any, expires_at: str) -> None:
    """检索结果写入/覆盖（同 key 重查覆盖刷新 TTL），顺带清理过期行。

    created_at 一并刷新为本次抓取时刻：读侧用它按「当前 TTL」裁剪历史行
    （TTL 调小后，按旧 TTL 写入的存量行不会继续命中）。
    """
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO jobs_cache (cache_key, keyword, city, payload_json, expires_at) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE keyword = VALUES(keyword), city = VALUES(city), "
                "payload_json = VALUES(payload_json), expires_at = VALUES(expires_at), "
                "created_at = CURRENT_TIMESTAMP",
                (
                    cache_key, keyword, city,
                    json.dumps(payload, ensure_ascii=False), expires_at,
                ),
            )
            cur.execute("DELETE FROM jobs_cache WHERE expires_at < NOW()")
    except Exception as exc:
        _warn(f"岗位检索缓存写库失败：{exc}")


def load_jobs_cache(cache_key: str, max_age_days: int = 0) -> dict[str, Any] | None:
    """未过期缓存行（含 keyword/city/payload）；过期或无行返回 None。

    max_age_days > 0 时追加「抓取时刻不早于 N 天前」的过滤：expires_at 是写入时按当时
    TTL 算出的绝对时刻，TTL 调小后存量行仍会显示为未过期，故按 created_at 以当前 TTL 兜底裁剪。
    """
    days = max(0, int(max_age_days))
    sql = "SELECT keyword, city, payload_json FROM jobs_cache WHERE cache_key = %s AND expires_at > NOW()"
    params: tuple[Any, ...] = (cache_key,)
    if days:
        sql += " AND created_at > DATE_SUB(NOW(), INTERVAL %s DAY)"
        params += (days,)
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
    except Exception as exc:
        _warn(f"岗位检索缓存读库失败：{exc}")
        return None
    if not row:
        return None
    return {
        "keyword": row[0],
        "city": row[1],
        "payload": json.loads(row[2]) if row[2] else [],
    }


def save_job_detail(security_id: str, keyword: str, payload: Any, expires_at: str) -> None:
    """JD 详情写入/覆盖，顺带清理过期行。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO job_details (security_id, keyword, payload_json, expires_at) "
                "VALUES (%s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE keyword = VALUES(keyword), "
                "payload_json = VALUES(payload_json), expires_at = VALUES(expires_at)",
                (
                    security_id, keyword,
                    json.dumps(payload, ensure_ascii=False), expires_at,
                ),
            )
            cur.execute("DELETE FROM job_details WHERE expires_at < NOW()")
    except Exception as exc:
        _warn(f"JD 详情缓存写库失败：{exc}")


def load_job_detail(security_id: str) -> Any | None:
    """未过期 JD 详情 payload；过期或无行返回 None。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT payload_json FROM job_details "
                "WHERE security_id = %s AND expires_at > NOW()",
                (security_id,),
            )
            row = cur.fetchone()
    except Exception as exc:
        _warn(f"JD 详情缓存读库失败：{exc}")
        return None
    return json.loads(row[0]) if row and row[0] else None


def save_job_map(map_key: str, keyword: str, city: str, payload: Any, expires_at: str) -> None:
    """考点地图报告写入/覆盖，顺带清理过期行。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO job_maps (map_key, keyword, city, payload_json, expires_at) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE keyword = VALUES(keyword), city = VALUES(city), "
                "payload_json = VALUES(payload_json), expires_at = VALUES(expires_at)",
                (
                    map_key, keyword, city,
                    json.dumps(payload, ensure_ascii=False), expires_at,
                ),
            )
            cur.execute("DELETE FROM job_maps WHERE expires_at < NOW()")
    except Exception as exc:
        _warn(f"考点地图缓存写库失败：{exc}")


def load_job_map(map_key: str) -> Any | None:
    """未过期考点地图报告；过期或无行返回 None。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT payload_json FROM job_maps "
                "WHERE map_key = %s AND expires_at > NOW()",
                (map_key,),
            )
            row = cur.fetchone()
    except Exception as exc:
        _warn(f"考点地图缓存读库失败：{exc}")
        return None
    return json.loads(row[0]) if row and row[0] else None


def save_daily_practice(
    practice_id: str, user_id: str, practice_date: str,
    question_ids: list[str], done_ids: list[str],
) -> None:
    """每日一练写入/覆盖（一人一天一行；判分由 practice/submit 链路负责，此处只记进度）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO daily_practices (id, user_id, practice_date, question_ids, done_ids) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE id = VALUES(id), question_ids = VALUES(question_ids), "
                "done_ids = VALUES(done_ids)",
                (
                    practice_id, user_id, practice_date,
                    json.dumps(question_ids, ensure_ascii=False),
                    json.dumps(done_ids, ensure_ascii=False),
                ),
            )
    except Exception as exc:
        _warn(f"每日一练写库失败：{exc}")


def load_daily_practice(user_id: str, practice_date: str) -> dict[str, Any] | None:
    """当日每日一练行；无行返回 None。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, question_ids, done_ids FROM daily_practices "
                "WHERE user_id = %s AND practice_date = %s",
                (user_id, practice_date),
            )
            row = cur.fetchone()
    except Exception as exc:
        _warn(f"每日一练读库失败：{exc}")
        return None
    if not row:
        return None
    return {
        "id": row[0],
        "questionIds": json.loads(row[1]) if row[1] else [],
        "doneIds": json.loads(row[2]) if row[2] else [],
    }


def save_pipeline_card(card: dict[str, Any]) -> None:
    """求职看板卡写入/覆盖（一卡一行，全量 upsert）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO job_pipeline (id, user_id, stage, security_id, keyword, job_name, "
                "brand, city, salary, platform, match_score, set_id, interview_at, note, in_trash) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE stage=VALUES(stage), security_id=VALUES(security_id), "
                "keyword=VALUES(keyword), job_name=VALUES(job_name), brand=VALUES(brand), "
                "city=VALUES(city), salary=VALUES(salary), platform=VALUES(platform), "
                "match_score=VALUES(match_score), set_id=VALUES(set_id), "
                "interview_at=VALUES(interview_at), note=VALUES(note), in_trash=VALUES(in_trash)",
                (
                    card["id"], card["userId"], card.get("stage", "applied"),
                    card.get("securityId", ""), card.get("keyword", ""),
                    card.get("jobName", ""), card.get("brand", ""), card.get("city", ""),
                    card.get("salary", ""), card.get("platform", "zhipin"),
                    card.get("matchScore"), card.get("setId", ""),
                    card.get("interviewAt") or None, card.get("note", ""),
                    1 if card.get("inTrash") else 0,
                ),
            )
    except Exception as exc:
        _warn(f"求职看板写库失败：{exc}")


def load_pipeline(user_id: str) -> list[dict[str, Any]] | None:
    """某用户全部看板卡（含回收站）；库不可用返回 None（调用方保持内存态）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, stage, security_id, keyword, job_name, brand, city, salary, "
                "platform, match_score, set_id, interview_at, note, in_trash, created_at "
                "FROM job_pipeline WHERE user_id = %s ORDER BY created_at ASC",
                (user_id,),
            )
            rows = cur.fetchall()
    except Exception as exc:
        _warn(f"求职看板读库失败：{exc}")
        return None
    cards: list[dict[str, Any]] = []
    for r in rows:
        cards.append({
            "id": r[0],
            "userId": user_id,
            "stage": r[1],
            "securityId": r[2] or "",
            "keyword": r[3] or "",
            "jobName": r[4] or "",
            "brand": r[5] or "",
            "city": r[6] or "",
            "salary": r[7] or "",
            "platform": r[8] or "zhipin",
            "matchScore": r[9],
            "setId": r[10] or "",
            "interviewAt": r[11].strftime("%Y-%m-%d") if r[11] else "",
            "note": r[12] or "",
            "inTrash": bool(r[13]),
            "createdAt": r[14].strftime("%Y-%m-%d %H:%M") if r[14] else "",
        })
    return cards


def delete_pipeline_card(card_id: str) -> None:
    """彻底删除看板卡（回收站永久删除）。"""
    try:
        conn = connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM job_pipeline WHERE id = %s", (card_id,))
    except Exception as exc:
        _warn(f"求职看板删库失败：{exc}")
