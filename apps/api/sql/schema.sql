-- AI 智慧面试宝典 · 题库持久化建库脚本（手动执行一次即可）
-- 要求 MySQL 5.7+（用到 JSON 类型）；建议 8.0。
-- 执行方式（任选其一）：
--   1) 命令行：mysql -u root -p < apps/api/sql/schema.sql
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
