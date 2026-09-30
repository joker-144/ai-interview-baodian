-- AI 智慧面试宝典 · 持久化建库脚本（手动执行一次即可，可重复执行：全部 CREATE IF NOT EXISTS）
-- 要求 MySQL 5.7+（用到 JSON 类型）；建议 8.0。
-- 执行方式（任选其一）：
--   1) 命令行（PowerShell 不支持 < 重定向）：mysql -u root -p --default-character-set=utf8mb4 -e "source apps/api/sql/schema.sql"
--   2) 在 Navicat / DataGrip 等工具里新建查询，整段执行
-- 执行完成后，把连接密码填到 apps/api/config/db.json 即可生效（后端下次启动时加载）。
--
-- 二期（V2.7）新增：study_plans（今日学习计划）、exam_records（模拟考试）、notifications（站内通知）；users 表新增 last_checkin_date 列。
-- 三期（V2.8）新增：jobs_cache（岗位检索缓存，四期增补起 TTL 收紧为 1 天）、job_details（JD 详情缓存）、job_maps（考点地图缓存）（后两者 TTL 7 天），均惰性过期刷新；
--   daily_practices（每日一练，惰性 10 题 + 打卡联动）。
-- 二期（V2.7）变更：resumes 表改为多版本结构（resume_id 主键 + version 递增 + is_optimized 标记）。
--   旧库升级：后端首次连接时自动检测旧结构并迁移（旧单行 -> version=1 的第一版），无需手工操作；
--   若自动迁移失败，可按以下 SQL 手工执行：
--     RENAME TABLE resumes TO resumes_legacy;
--     （按本文件下方 resumes 新定义建表）
--     INSERT INTO resumes (resume_id, user_id, version, is_optimized, file_name, text, summary, analysis, created_at)
--       SELECT CONCAT(user_id, '-v1'), user_id, 1, 0, file_name, text, summary, analysis, updated_at
--       FROM resumes_legacy;
--     DROP TABLE resumes_legacy;
-- 已按旧版建过库的库无需重跑：后端启动时会自动补建新表与新列（见 app/db.py ensure_upgrade），
-- 本文件保持与库结构同步的最新全量版本。

CREATE DATABASE IF NOT EXISTS ai_interview_baodian
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE ai_interview_baodian;

-- 题集表
CREATE TABLE IF NOT EXISTS question_sets (
  id             VARCHAR(64)  NOT NULL COMMENT '题集 id（set-xxxxxxxx / 种子 set-1）',
  source         VARCHAR(32)  NOT NULL DEFAULT 'resume' COMMENT '来源：resume / job_search / jd_target / mock_interview',
  owner_id       VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '归属用户 user_id（空=公共题库/预置种子，所有人可见；非空=私有题集，仅本人可见）',
  title          VARCHAR(255) NOT NULL COMMENT '题集标题',
  question_count INT          NOT NULL DEFAULT 0 COMMENT '题量（冗余计数，随写入维护）',
  created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '真实更新时间（题库卡片右上角展示）',
  PRIMARY KEY (id),
  KEY idx_question_sets_owner (owner_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '题集';

-- 题目表
CREATE TABLE IF NOT EXISTS questions (
  id               VARCHAR(64)  NOT NULL COMMENT '题目 id（q-gen-xxxxxxxx / 种子 q-p1）',
  set_id           VARCHAR(64)  NOT NULL COMMENT '所属题集 id',
  type             VARCHAR(32)  NOT NULL DEFAULT 'single_choice' COMMENT '题型：single_choice / multi_choice / judge / short_answer / scenario',
  category         VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '题目分类（专业技术 / 项目深挖 / 场景设计…）',
  stem             TEXT         NOT NULL COMMENT '题干',
  options          JSON         NOT NULL COMMENT '客观题选项数组（主观题为 []）',
  answer           VARCHAR(255) NOT NULL COMMENT '参考答案（如 B / 正确 / AB）',
  reference_answer MEDIUMTEXT   NULL COMMENT '参考回答（主观题作答话术）',
  explanation      MEDIUMTEXT   NULL COMMENT '解析',
  knowledge_tags   JSON         NOT NULL COMMENT '知识点标签数组',
  difficulty       TINYINT      NOT NULL DEFAULT 2 COMMENT '难度 1~3',
  site_correct_rate INT         NULL COMMENT '全站答对率（作答 <100 次为 NULL，前端不展示）',
  created_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  PRIMARY KEY (id),
  KEY idx_questions_set (set_id),
  CONSTRAINT fk_questions_set FOREIGN KEY (set_id) REFERENCES question_sets (id) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '题目';

-- ============================================================
-- 用户维度数据（一期单演示账号 demo-user；接 JWT 后每用户一行/一组行）
-- 说明：progress / wrong_items 故意不加指向 questions 的外键——
-- 收藏与错题允许短暂悬挂引用（与内存模型一致），删除题集时按 set_id 显式清理。
-- ============================================================

-- 用户资料 + 偏好 + 注销申请（一行全量 upsert，见 app/db.py save_user）
CREATE TABLE IF NOT EXISTS users (
  user_id                  VARCHAR(64)  NOT NULL COMMENT 'JWT sub；演示账号固定 demo-user',
  account                  VARCHAR(64)  NULL COMMENT '登录账号（用户名/手机号，唯一）',
  password_hash            VARCHAR(255) NOT NULL DEFAULT '' COMMENT 'pbkdf2_sha256$iter$salt$hash；微信/短信注册的账号为空',
  wechat_openid            VARCHAR(64)  NULL COMMENT '微信登录 openid（二期接入，一期预留）',
  name                     VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '姓名',
  avatar_text              VARCHAR(16)  NOT NULL DEFAULT '' COMMENT '头像字（姓名首字）',
  target_role              VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '目标岗位',
  years                    INT          NOT NULL DEFAULT 0 COMMENT '工作年限',
  streak                   INT          NOT NULL DEFAULT 0 COMMENT '连续学习天数',
  total_answered           INT          NOT NULL DEFAULT 0 COMMENT '累计答题数',
  correct_rate             INT          NOT NULL DEFAULT 0 COMMENT '正确率（%）',
  phone                    VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '脱敏手机号回显',
  wechat_bound             TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '微信绑定',
  review_reminder_enabled  TINYINT(1)   NOT NULL DEFAULT 1 COMMENT '复习提醒开关',
  review_reminder_time     VARCHAR(8)   NOT NULL DEFAULT '20:00' COMMENT '复习提醒时间 HH:mm',
  deactivation_status      VARCHAR(16)  NULL COMMENT '注销状态：cooling_off / executed；NULL=无申请',
  deactivation_requested_at  DATETIME   NULL COMMENT '注销申请时间',
  deactivation_cooling_until DATETIME   NULL COMMENT '冷静期截止（7 天）',
  deactivation_reason      VARCHAR(500) NOT NULL DEFAULT '' COMMENT '注销原因',
  deactivation_scopes      JSON         NULL COMMENT '注销将清理的数据范围（展示用）',
  deactivation_executed_at DATETIME     NULL COMMENT '清理执行时间',
  last_checkin_date        DATE         NULL COMMENT '最近一次学习计划打卡达成日（当 done/total ≥ 2/3 时记 streak+1，同日不重复计）',
  updated_at               DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id),
  UNIQUE KEY uk_users_account (account),
  UNIQUE KEY uk_users_openid (wechat_openid)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '用户资料 / 偏好 / 注销 / 账号';

-- 刷题进度：一次作答一行
CREATE TABLE IF NOT EXISTS practice_progress (
  user_id      VARCHAR(64)  NOT NULL,
  set_id       VARCHAR(64)  NOT NULL,
  question_id  VARCHAR(64)  NOT NULL,
  choice       VARCHAR(255) NOT NULL COMMENT '用户选项（如 B / AB）',
  updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, set_id, question_id),
  KEY idx_progress_user (user_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '刷题进度';

-- 作答事件明细（append-only）：全站答对率统计源，每题聚合 ≥100 次才展示（app/store.py MIN_SAMPLE）
-- 故意不加指向 questions 的外键——与进度/错题一致，允许短暂悬挂引用（统计只应用到在库题目）
CREATE TABLE IF NOT EXISTS answer_events (
  id          BIGINT       NOT NULL AUTO_INCREMENT,
  user_id     VARCHAR(64)  NOT NULL COMMENT '作答用户（JWT sub）',
  set_id      VARCHAR(64)  NOT NULL COMMENT '题集 id',
  question_id VARCHAR(64)  NOT NULL COMMENT '题目 id',
  choice      VARCHAR(255) NOT NULL COMMENT '用户选项（如 B / AB）',
  is_correct  TINYINT(1)   NOT NULL COMMENT '是否答对',
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '作答时间',
  PRIMARY KEY (id),
  KEY idx_events_question (question_id),
  KEY idx_events_user (user_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '作答事件（全站答对率统计源）';

-- 错题本：nextReviewLabel 由 stage 推导，不落库；last_wrong_at 落真实时间
CREATE TABLE IF NOT EXISTS wrong_items (
  user_id       VARCHAR(64) NOT NULL,
  question_id   VARCHAR(64) NOT NULL,
  set_id        VARCHAR(64) NOT NULL,
  reason        VARCHAR(32) NOT NULL DEFAULT 'concept' COMMENT '错因三分法：concept / misread / blind_spot',
  wrong_count   INT         NOT NULL DEFAULT 1 COMMENT '累计答错次数',
  stage         TINYINT     NOT NULL DEFAULT 0 COMMENT '艾宾浩斯阶段 0~4（1/2/4/7/15 天）',
  review_streak INT         NOT NULL DEFAULT 0 COMMENT '复习连续答对次数，满 3 提前毕业',
  mastered      TINYINT(1)  NOT NULL DEFAULT 0 COMMENT '已掌握（连对 3 次或到顶档）',
  last_wrong_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '最近答错时间',
  PRIMARY KEY (user_id, question_id),
  KEY idx_wrong_user_set (user_id, set_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '错题本';

-- 收藏
CREATE TABLE IF NOT EXISTS favorites (
  user_id      VARCHAR(64) NOT NULL,
  question_id  VARCHAR(64) NOT NULL,
  created_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, question_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '收藏';

-- 简历多版本（二期 V2.7）：每份解析产物一行，version 递增；
-- is_optimized=1 的行为「AI 一键优化」生成的优化版（以体检结论 + 原简历为输入重写）。
CREATE TABLE IF NOT EXISTS resumes (
  resume_id    VARCHAR(64)  NOT NULL COMMENT '简历版本 id（res-xxxxxxxx）',
  user_id      VARCHAR(64)  NOT NULL COMMENT '所属用户（JWT sub）',
  version      INT          NOT NULL DEFAULT 1 COMMENT '版本号（同一用户内递增）',
  is_optimized TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否 AI 优化版',
  file_name    VARCHAR(255) NOT NULL DEFAULT '' COMMENT '上传文件名（优化版加后缀）',
  text         MEDIUMTEXT   NULL COMMENT '抽取后的简历原文（优化版为重写文本，供出题提示词，不对外回显）',
  summary      MEDIUMTEXT   NULL COMMENT 'AI 画像摘要（供出题提示词）',
  analysis     JSON         NULL COMMENT '结构化解析结果（年限/岗位/维度/预估题量/体检 checkup）',
  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  PRIMARY KEY (resume_id),
  KEY idx_resumes_user (user_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '简历解析产物（多版本）';

-- 今日学习计划（二期）：每人每天一组任务；当日无计划时由后端惰性 AI 生成（无定时器口径）
CREATE TABLE IF NOT EXISTS study_plans (
  id             VARCHAR(64)  NOT NULL COMMENT '计划任务 id（plan-xxxxxxxx）',
  user_id        VARCHAR(64)  NOT NULL COMMENT '所属用户（JWT sub）',
  plan_date      DATE         NOT NULL COMMENT '计划日期（当日为一组）',
  type           VARCHAR(16)  NOT NULL DEFAULT 'practice' COMMENT '任务类型：practice / review / interview / jd_set / resume_check',
  title          VARCHAR(255) NOT NULL COMMENT '任务标题',
  est_minutes    INT          NOT NULL DEFAULT 15 COMMENT '时长预估（分钟）',
  done           TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否完成',
  auto_generated TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否 AI 生成（手动添加为 0）',
  ref_id         VARCHAR(64)  NULL COMMENT '关联资源 id（practice 任务对应题集 id，其余 NULL）',
  created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_plans_user_date (user_id, plan_date)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '今日学习计划';

-- 模拟考试（二期）：一卷一行；作答逐题增量覆盖写 answered_detail（断网/刷新恢复基础），
-- question_ids 为组卷快照（恢复 running 时需原卷，不随题库变动）。
CREATE TABLE IF NOT EXISTS exam_records (
  id              VARCHAR(64)  NOT NULL COMMENT '考试 id（exam-xxxxxxxx）',
  user_id         VARCHAR(64)  NOT NULL COMMENT '所属用户（JWT sub）',
  set_id          VARCHAR(64)  NOT NULL COMMENT '出卷题集 id',
  bucket_role     VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '岗位分桶（取 target_role，空卷通用桶）',
  total           INT          NOT NULL DEFAULT 0 COMMENT '卷面题量',
  question_ids    JSON         NOT NULL COMMENT '组卷快照（题目 id 顺序数组）',
  answered_detail JSON         NULL COMMENT '已答明细 [{questionId, choice, timeSec, correct}]，逐题覆盖写',
  score           INT          NULL COMMENT '百分制得分（done 后回写）',
  duration_sec    INT          NOT NULL DEFAULT 0 COMMENT '有效作答用时（不含暂停）',
  paused_sec      INT          NOT NULL DEFAULT 0 COMMENT '累计暂停秒数（hidden 超 10 分钟才计）',
  status          VARCHAR(16)  NOT NULL DEFAULT 'running' COMMENT 'running / paused / done / abandoned',
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '开考时间',
  finished_at     DATETIME     NULL COMMENT '交卷时间',
  PRIMARY KEY (id),
  KEY idx_exams_user (user_id, status),
  KEY idx_exams_role (bucket_role, status)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '模拟考试';

-- 站内通知（二期）：触发源为出题任务完成 / 模考报告生成 / 复习到期汇总（GET 时惰性写，无定时器口径）。
-- 注意：read 是 MySQL 保留字，列名用反引号。
CREATE TABLE IF NOT EXISTS notifications (
  id           VARCHAR(64)  NOT NULL COMMENT '通知 id（ntf-xxxxxxxx）',
  user_id      VARCHAR(64)  NOT NULL COMMENT '所属用户（JWT sub）',
  type         VARCHAR(32)  NOT NULL COMMENT 'generate_done / exam_report / review_due',
  payload_json JSON         NULL COMMENT '通知负载（跳转参数等，结构随 type）',
  `read`       TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否已读',
  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_notifications_user (user_id, `read`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '站内通知';

-- 出题任务（中断恢复）：创建时插入、每批生成后更新进度、完成时写终态；
-- 后端重启时 status='running' 的任务被标记为 interrupted，前端提示「继续补齐剩余题目」。
-- 故意不加指向 question_sets 的外键——删除题集时任务允许悬挂，恢复查询时校验题集存在性。
CREATE TABLE IF NOT EXISTS generate_tasks (
  task_id     VARCHAR(64)  NOT NULL COMMENT '任务 id（gen-xxxxxxxx）',
  user_id     VARCHAR(64)  NOT NULL COMMENT '发起用户（JWT sub）',
  set_id      VARCHAR(64)  NOT NULL COMMENT '目标题集 id',
  total       INT          NOT NULL DEFAULT 0 COMMENT '计划题量',
  generated_cnt INT       NOT NULL DEFAULT 0 COMMENT '已产出题量（每批更新；generated 为保留字故缩写）',
  dropped_cnt INT         NOT NULL DEFAULT 0 COMMENT '淘汰题量（结构校验/答案复判/去重）',
  status      VARCHAR(16)  NOT NULL DEFAULT 'running' COMMENT 'running / done / interrupted',
  error       VARCHAR(500) NULL COMMENT '失败原因',
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  finished_at DATETIME     NULL COMMENT '结束时间（done / interrupted）',
  PRIMARY KEY (task_id),
  KEY idx_gen_tasks_user (user_id, status)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '出题任务（中断恢复）';

-- 岗位检索缓存（三期）：key = hash(关键词+城市)，payload 为分层采样后的岗位卡列表；
-- TTL 1 天（四期增补调整，原 7 天；新鲜度优先，store.JOBS_CACHE_TTL_DAYS 为唯一口径），惰性过期刷新取代定时任务；
-- 读时校验 expires_at 并按 created_at 以当前 TTL 兜底裁剪（TTL 调小后存量行不会继续命中），写时顺带清理过期行。
CREATE TABLE IF NOT EXISTS jobs_cache (
  cache_key   VARCHAR(64)  NOT NULL COMMENT '缓存 key（sha256(关键词|城市)）',
  keyword     VARCHAR(128) NOT NULL COMMENT '检索关键词（展示用）',
  city        VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '城市（空为不限）',
  payload_json JSON        NOT NULL COMMENT '采样后的岗位卡列表',
  expires_at  DATETIME     NOT NULL COMMENT '过期时间（写入 + 1 天）',
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '最近一次抓取时刻（覆盖写入时刷新）',
  PRIMARY KEY (cache_key),
  KEY idx_jobs_cache_exp (expires_at)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '岗位检索缓存（TTL 1 天）';

-- 岗位 JD 详情缓存（三期）：security_id 一行一份全量 JD；TTL 同 7 天，供聚合分析与引擎 C 链接通道复用。
CREATE TABLE IF NOT EXISTS job_details (
  security_id VARCHAR(512) NOT NULL COMMENT 'BOSS 岗位 security_id（实测可长达 ~340 字符）',
  keyword     VARCHAR(128) NOT NULL DEFAULT '' COMMENT '来源检索关键词（溯源）',
  payload_json JSON        NOT NULL COMMENT 'JD 全量详情（岗位名/公司/薪资/描述/技能标签等）',
  expires_at  DATETIME     NOT NULL COMMENT '过期时间（写入 + 7 天）',
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (security_id),
  KEY idx_job_details_exp (expires_at)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '岗位 JD 详情缓存（TTL 7 天）';

-- 考点地图报告缓存（三期）：聚合分析产物，key = hash(关键词+城市)；
-- 独立于生成设置——设置变更只重跑出题不重跑聚合；TTL 7 天。
CREATE TABLE IF NOT EXISTS job_maps (
  map_key     VARCHAR(64)  NOT NULL COMMENT '缓存 key（sha256(关键词|城市)）',
  keyword     VARCHAR(128) NOT NULL COMMENT '检索关键词',
  city        VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '城市（空为不限）',
  payload_json JSON        NOT NULL COMMENT '聚合报告（topSkills/duties/salaryInsight/tiers）',
  expires_at  DATETIME     NOT NULL COMMENT '过期时间（写入 + 7 天）',
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (map_key),
  KEY idx_job_maps_exp (expires_at)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '考点地图报告缓存（TTL 7 天）';

-- 每日一练（三期）：每人每天一行，当日首次访问惰性生成 10 题（薄弱 60% + 随机 40%，不调 LLM）；
-- 判分复用 /api/practice/submit（题目原 setId），此处只记完成进度；全部完成计入打卡（last_checkin_date）。
CREATE TABLE IF NOT EXISTS daily_practices (
  id             VARCHAR(64)  NOT NULL COMMENT '每日一练 id（daily-xxxxxxxx）',
  user_id        VARCHAR(64)  NOT NULL COMMENT '所属用户（JWT sub）',
  practice_date  DATE         NOT NULL COMMENT '练习日期',
  question_ids   JSON         NOT NULL COMMENT '当日 10 题快照（生成后不变）',
  done_ids       JSON         NULL COMMENT '已完成题目 id 数组',
  created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_daily_user_date (user_id, practice_date)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '每日一练';

-- 求职看板（四期）：一卡一行，四列状态机 applied/written/interview/offer + 回收站软删（in_trash）。
-- 岗位来源两种：zhipin 卡（security_id 非空，由检索结果回填岗位信息 + 两阶段漏斗匹配分 + 挂接关键词岗位市场题集）；
-- 外部平台/手动卡（security_id 空，用户手动录入，状态与 HR 回复手动流转，不触达平台——只读合规口径）。
-- interview_at 非空且 stage=interview 时，GET 惰性触发 interview_prep 复习提醒（当日去重）。
CREATE TABLE IF NOT EXISTS job_pipeline (
  id            VARCHAR(64)  NOT NULL COMMENT '看板卡 id（pipe-xxxxxxxx）',
  user_id       VARCHAR(64)  NOT NULL COMMENT '所属用户（JWT sub）',
  stage         VARCHAR(16)  NOT NULL DEFAULT 'applied' COMMENT '阶段：applied/written/interview/offer',
  security_id   VARCHAR(512) NOT NULL DEFAULT '' COMMENT 'BOSS 岗位 security_id（手动卡为空）',
  keyword       VARCHAR(128) NOT NULL DEFAULT '' COMMENT '来源检索关键词（溯源 + 题集归并）',
  job_name      VARCHAR(255) NOT NULL DEFAULT '' COMMENT '岗位名',
  brand         VARCHAR(255) NOT NULL DEFAULT '' COMMENT '公司',
  city          VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '城市',
  salary        VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '薪资（展示用文本）',
  platform      VARCHAR(16)  NOT NULL DEFAULT 'zhipin' COMMENT '来源平台：zhipin/zhilian/51job/liepin/manual',
  match_score   INT          NULL COMMENT '两阶段漏斗匹配分 0~100（手动卡为 NULL）',
  set_id        VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '挂接的定向题库题集 id（空为未挂接）',
  interview_at  DATE         NULL COMMENT '面试日期（驱动复习提醒联动）',
  note          TEXT         NULL COMMENT 'HR 回复 / 跟进备注（手动记录）',
  in_trash      TINYINT      NOT NULL DEFAULT 0 COMMENT '1=回收站（软删）',
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_pipeline_user_stage (user_id, stage),
  KEY idx_pipeline_user_trash (user_id, in_trash)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '求职看板（四列状态机）';

