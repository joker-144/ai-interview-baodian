-- AI 智慧面试宝典 · 持久化建库脚本（手动执行一次即可，可重复执行：全部 CREATE IF NOT EXISTS）
-- 要求 MySQL 5.7+（用到 JSON 类型）；建议 8.0。
-- 执行方式（任选其一）：
--   1) 命令行（PowerShell 不支持 < 重定向）：mysql -u root -p --default-character-set=utf8mb4 -e "source apps/api/sql/schema.sql"
--   2) 在 Navicat / DataGrip 等工具里新建查询，整段执行
-- 执行完成后，把连接密码填到 apps/api/config/db.json 即可生效（后端下次启动时加载）。

CREATE DATABASE IF NOT EXISTS ai_interview_baodian
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE ai_interview_baodian;

-- 题集表
CREATE TABLE IF NOT EXISTS question_sets (
  id             VARCHAR(64)  NOT NULL COMMENT '题集 id（set-xxxxxxxx / 种子 set-1）',
  source         VARCHAR(32)  NOT NULL DEFAULT 'resume' COMMENT '来源：resume / job_search / jd_target / mock_interview',
  title          VARCHAR(255) NOT NULL COMMENT '题集标题',
  question_count INT          NOT NULL DEFAULT 0 COMMENT '题量（冗余计数，随写入维护）',
  created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '真实更新时间（题库卡片右上角展示）',
  PRIMARY KEY (id)
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

-- 最近一份简历的解析产物（原文 + 画像摘要 + 结构化结果；一期仅存最新一份）
CREATE TABLE IF NOT EXISTS resumes (
  user_id    VARCHAR(64)  NOT NULL,
  file_name  VARCHAR(255) NOT NULL DEFAULT '' COMMENT '上传文件名',
  text       MEDIUMTEXT   NULL COMMENT '抽取后的简历原文（供出题提示词，不对外回显）',
  summary    MEDIUMTEXT   NULL COMMENT 'AI 画像摘要（供出题提示词）',
  analysis   JSON         NULL COMMENT '结构化解析结果（年限/岗位/维度/预估题量）',
  updated_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '简历解析产物（最新一份）';

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

