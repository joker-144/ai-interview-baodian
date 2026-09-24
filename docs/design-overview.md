# 设计概要（脱敏版）

> 本文档为公开仓库版设计概要。完整版产品文档（含排期、成本测算与商业化设计）不在开源范围内。

## 产品定位

面向求职者的 AI 面试训练应用（结构化题库 + 智能出题）。题目由 AI 根据**用户简历**和**目标岗位真实 JD** 动态生成，每题附参考回答与深度解析。

## 核心模块

### 1. 三大出题引擎

- **引擎 A（简历分析）**：文档解析 → LLM 结构化 → 能力维度评估 → Planner 按覆盖矩阵规划 → 并行生成 → Critic 校验 → 客观题答案二次验证 → Embedding 去重 → 入库。
- **引擎 B（岗位检索）**：boss-agent-cli（MCP）检索真实在招岗位 → 分层采样 → JD 聚合分析 → 市场考点题库。
- **引擎 C（JD 定向）**：链接/截图/文本三通道获取 JD → 公司背景 RAG → 与简历交叉 → 匹配度报告 + 定向冲刺包。

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

### 5. 平台侧模型配置管理端（内部工具）

- 路由 `/admin/models`，独立鉴权（不复用 C 端账号体系），不进 C 端导航；
- 四类分层模型 + 语音（主 / 轻量 / 多模态 / Embedding / ASR-TTS）的供应商、模型名、API Key、base_url 与分层推理参数在线配置；
- 连通性自测（回显延迟 / token 用量 / 失败原因）、保存即热生效（无需重启）、历史快照一键回滚、变更审计（除「模型发现」外全部打点，每页 10 条分页呈现，上限 200 条）；
- API Key 密文存储（一期为应用层混淆，生产换 KMS / Fernet，密钥不入仓），接口与日志**只回显掩码**（`sk-****abcd`），回传掩码视为未修改；配置 / 历史快照 / 审计统一落盘 `apps/api/config/llm.json`（真实文件 .gitignore 忽略，仓内为 Key 全空的 `.example` 模板），前端管理端直连后端 `/api/admin/*`（不走 localStorage）；
- 模型选择不开放给 C 端用户，以保住分层用模与缓存摊薄策略（缓存键含模型标识）；用户侧只有业务级生成设置（题量 / 难度 / 是否附答案解析）；
- **供应商联动与模型发现**：供应商以注册表管理（12 家，含云端 / 自建 Ollama / 本地 / 自定义），选定即自动回填 `base_url`（仍可手改，兼容自建网关）；**填入 API Key 后自动拉取该账号可用模型**（输入停顿防抖约 700ms，以「供应商 + base_url + Key」为指纹去重，无需点按钮，保留「重新拉取」作强制刷新），拉取失败回落常用候选并给出可读原因，**模型名始终允许手动输入**；候选以自控下拉面板呈现（点输入框或右侧箭头即展开，可点选 / 过滤 / 点外部收起），不用原生 `datalist`（部分浏览器点击不展开）；切换供应商即清空旧模型名（属旧供应商，继续用会 404），发现后若未选中则自动选中首个候选；
- **本地模型内置**：向量模型下载到 `apps/api/models/`（权重入仓跟踪、克隆即用；`python scripts/download_models.py` 多源下载 + 写 `manifest.json`），服务端离线加载推理，不出网、不计费；Embedding 层默认走本地 bge-small-zh-v1.5（dim 512），其「测试连接」为真实推理并校验相似度区分度；生成式分层不本地部署。

## 数据模型核心表（节选）

```
users / resumes(file_key→对象存储) / question_sets(resume_id, security_id)
questions(embedding vector) / user_question_state / review_schedule
answer_events(按月分区, 统计事实源) / exam_records / jobs_cache(TTL 7d)
study_plans / resume_reports / interview_answers / interview_turns
job_pipeline(四列状态机) / agent_checkpoints / agent_artifacts
llm_config(分层模型配置, api_key 密文) / llm_config_history(可回滚快照)
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
2. 简历脱敏后送 LLM；语音不留录音只留转写稿；支持一键注销物理删除（Web 端入口为 P14 `/me`：二次确认并逐条明示删除范围 → 7 天冷静期可撤回 → 到期清理不可逆）；
3. Boss 数据仅受控只读检索，不做批量采集/自动投递，页面展示数据来源声明；
4. 全站统计仅聚合数据，低样本（<100 次作答）不展示。
