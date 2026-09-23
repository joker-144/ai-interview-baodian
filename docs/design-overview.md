# 设计概要（脱敏版）

> 本文档为公开仓库版设计概要。完整版产品文档（含排期、成本测算与商业化设计）不在开源范围内。

## 产品定位

面向求职者的「驾考宝典式」AI 面试备考应用。题目由 AI 根据**用户简历**和**目标岗位真实 JD** 动态生成，每题附参考回答与深度解析。

## 核心模块

### 1. 三大出题引擎

- **引擎 A（简历分析）**：文档解析 → LLM 结构化 → 能力维度评估 → Planner 按覆盖矩阵规划 → 并行生成 → Critic 校验 → 客观题答案二次验证 → Embedding 去重 → 入库。
- **引擎 B（岗位检索）**：boss-agent-cli（MCP）检索真实在招岗位 → 分层采样 → JD 聚合分析 → 市场考点题库。
- **引擎 C（JD 定向）**：链接/截图/文本三通道获取 JD → 公司背景 RAG → 与简历交叉 → 匹配度报告 + 定向备战包。

### 2. Agent 编排（LangGraph 状态机）

- 工作流式编排，节点固定：`输入源解析 → Planner → Generator×N → Critic(回炉≤2) → Verifier → Dedup → 入库+流式推送`；
- 每个 Node 后写任务级 Checkpoint（`agent_checkpoints`），worker 崩溃续跑不重算；
- LLM 调用强制 JSON Schema 结构化输出；
- 批间记忆 = 简历摘要 + 规划结果 + 已出题摘要（≤ 8K token/批）；
- 中间产物落 `agent_artifacts` 表带缓存键复用（缓存键 = hash(模型 + Prompt 版本 + 输入摘要)）。

### 3. 学习强化

- 错题本：错因三分法（概念不清/审题失误/知识盲区）+ 艾宾浩斯五档（1/2/4/7/15 天）+ 连对 3 次归档；
- 今日学习计划（AI 生成 3~5 项）、模拟考试、掌握度图谱、学习周报。

### 4. 语音模拟面试

WebSocket 流式 ASR + TTS、AI 追问（复用题目 follow_up 链）、STAR 四维实时评估、命中关键词检测、复盘报告。

## 数据模型核心表（节选）

```
users / resumes(file_key→对象存储) / question_sets(resume_id, security_id)
questions(embedding vector) / user_question_state / review_schedule
answer_events(按月分区, 统计事实源) / exam_records / jobs_cache(TTL 7d)
study_plans / resume_reports / interview_answers / interview_turns
job_pipeline(四列状态机) / agent_checkpoints / agent_artifacts
```

## 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 前端 | Next.js + Taro | 双端同构，组件复用约 70% |
| 后端 | FastAPI + Celery + Redis | 分钟级出题任务异步化 + SSE 流式进度 |
| 编排 | LangGraph | 状态机回炉循环 + Checkpoint 持久化 |
| 检索 | boss-agent-cli（MIT） | 只读、节流、JSON 信封适合编排 |
| 存储 | PostgreSQL + pgvector | 关系 + 向量一库；对象存储放文件 |
| 语音 | 流式 ASR/TTS | 首字 < 800ms，TTS 按题预合成 |

## 质量与合规红线

1. 客观题答案双模型交叉验证，不一致题降权/待审；
2. 简历脱敏后送 LLM；语音不留录音只留转写稿；支持一键注销物理删除；
3. Boss 数据仅受控只读检索，不做批量采集/自动投递，页面展示数据来源声明；
4. 全站统计仅聚合数据，低样本（<100 次作答）不展示。
