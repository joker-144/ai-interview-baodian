# AI 面试宝典 · API（apps/api）

FastAPI 后端，对齐产品文档 4.5 接口。C 端为**真实链路**：分层 LLM 真实调用（简历解析 / 出题 / 体检评分 / AI 优化 / 学习计划与周报，均带降级路径）；数据**内存优先 + MySQL 尽力持久化**（`config/db.json`，连接失败自动降级回内存）；**管理端模型配置落盘**（`config/llm.json`，含混淆 Key，重启不丢）。

## 启动

```powershell
cd apps/api
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

- 接口文档（Swagger）：http://localhost:8000/docs
- 前端联调：`apps/web/lib/api.ts` 已全部直连 `http://127.0.0.1:8000`（管理端与 C 端均为真实 API，非 Mock）

## 结构

```
apps/api/
├── requirements.txt
├── config/          # 运行期配置：llm.json（分层模型 + 混淆 Key）+ db.json（MySQL 连接）；均以 .example 模板入仓，真实文件 .gitignore 忽略
├── sql/schema.sql   # MySQL 建库脚本（全部 CREATE IF NOT EXISTS / 容错 ALTER，可重复执行）
├── models/          # 本地模型权重目录（入仓跟踪，见下「本地模型」）+ manifest.json
└── app/
    ├── main.py        # FastAPI 入口 + CORS + 路由注册
    ├── config.py      # 配置（环境变量）
    ├── schemas.py     # Pydantic 模型（对齐前端 lib/types.ts）
    ├── models.py      # SQLAlchemy 表定义（预留）
    ├── store.py       # 内存数据仓库 + 种子数据（启动时从 db.py 水合 MySQL 已落库的用户数据）
    ├── db.py          # MySQL 持久化层（双模式：连接可用则读写 + _ensure_upgrade 幂等补建新表；不可用降级内存）
    ├── boss_cli.py    # 【三期】boss-agent-cli 子进程封装（JSON 信封解析 + 错误分类映射 + 节流附加保险）
    ├── llm.py         # 分层 LLM 调用（主模型/轻量模型/多模态，JSON 结构化输出 + 失败抛 LlmError）
    ├── analyzer.py    # 简历解析 + 能力维度评估 + 体检 checkup + AI 一键优化（LLM 失败走规则兜底）
    ├── generation.py  # 出题任务生命周期（后台任务 + SSE 观察者 + 终态写站内信）；【四期】引擎 B 两阶段评分漏斗（词法预筛 → LLM 深度匹配分）
    ├── providers.py   # LLM 供应商注册表 + 模型发现
    ├── local_models.py # 本地模型离线推理（懒加载单例 + 真实自测）
    ├── llm_config_file.py # 模型配置 / 历史快照 / 审计的落盘读写边界（原子写）
    └── routers/
        ├── auth.py         # 注册 / 登录（密码 + 手机验证码 + 微信，JWT）/ 我的信息
        ├── plans.py        # 今日学习计划（当日为空惰性 AI 生成 3~5 项）+ 打卡 streak
        ├── question_sets.py # 题集 CRUD + 出题任务（三通道统一入口、SSE 流式进度）
        ├── questions.py    # 题目查询 / 全站答对率（低样本保护）
        ├── practice.py     # 刷题进度 / 提交判分（错题自动收录 + answer_events）/ 收藏
        ├── wrong_book.py   # 错题本 / 艾宾浩斯复习
        ├── resumes.py      # 简历上传解析 + 多版本管理 + 体检报告 + AI 一键优化 + DOCX 下载
        ├── jobs.py         # 【三期】岗位检索（boss status / search / detail / 考点地图 map）
        ├── daily.py        # 【三期】每日一练（惰性生成 10 题：薄弱 60% + 随机 40%，打卡联动）
        ├── pipeline.py     # 【四期】求职看板（job_pipeline 四列状态机 + 回收站 + 趋势统计 + 面试临近提醒）
        ├── stats.py        # 本周统计（近 7 天作答柱状 + 三卡，answer_events 聚合）
        ├── reports.py      # 学习周报（趋势 / 薄弱知识点 TOP5 / AI 下周建议，按周缓存）
        ├── exams.py        # 模拟考试：组卷 / 答题增量落库 / 暂停恢复 / 交卷判分 / 模考报告 / 补强练习
        ├── notifications.py # 站内通知（出题完成 / 模考报告 / 复习到期 / 面试临近）
        ├── me.py           # 我的 / 设置（P14）：资料 + 统计 + 注销冷静期 + 数据导出
        └── admin.py        # 【管理端】分层模型配置 / 连通性自测 / 回滚 / 审计（文档 4.6.1）
```

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | 前端来源白名单（逗号分隔） |
| `MOCK_LLM` | `1` | **已废弃**（历史预留，当前代码无引用）；解析/出题/自测均为真实 LLM 链路 |
| `ADMIN_TOKEN` | `admin-dev-token` | 管理端令牌，请求头 `X-Admin-Token` 校验；生产必须改值并定期轮换 |
| `KEY_SECRET` | `aib-dev-key-secret-change-me` | API Key 存储密钥，不入仓；当前为 XOR+base64 **混淆**（非加密），生产需换 KMS / Fernet |
| `LOCAL_MODELS_DIR` | `apps/api/models` | 本地模型权重存放目录 |
| `LOCAL_MODEL_THREADS` | `2` | 本地推理线程数；受限环境（容器 / 低内存）调低可避免 OpenBLAS 分配失败 |

> MySQL 连接不走环境变量，见 `config/db.json`（模板 `db.json.example`）：`enabled=false`、密码错误或库不存在时打印告警并**自动降级内存模式**，其余功能不受影响。

## 本地模型（Embedding 默认离线推理）

```powershell
python scripts/download_models.py            # 下载默认模型 bge-small-zh-v1.5（约 91 MB）
python scripts/download_models.py --list     # 查看可下载模型
python scripts/download_models.py --model bge-large-zh-v1.5 --source modelscope
```

- 权重下载到 `apps/api/models/<model_id>/`，**入仓跟踪**（团队共享、克隆即用；单文件 91MB 低于 GitHub 100MB 上限）。
- 下载按 `ModelScope → hf-mirror → HuggingFace` 多源重试（实测 hf-mirror 的 `.safetensors` 会 302 到 xethub CDN 而超时，故优先 ModelScope）；完成后写 `manifest.json`，是管理端 `/api/admin/local-models` 的唯一数据源。
- 运行时 `sentence-transformers` 以 `local_files_only=True` 加载，懒加载 + 进程内单例，**不出网、不计费**。
- Embedding 层默认即 `provider=local / bge-small-zh-v1.5 / dim=512`；该层「测试连接」是**真实推理**，会校验同义句与无关句的相似度区分度。

## 接口清单（实际注册路径，与产品文档 4.5 对应）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /api/auth/register | 手机号 + 密码注册（返回 JWT） |
| POST | /api/auth/login | 密码登录（返回 JWT） |
| POST | /api/auth/phone-login | 手机号验证码登录 |
| POST | /api/auth/wechat-login | 微信登录 |
| GET | /api/auth/me | 当前用户信息 |
| GET | /api/plans | 今日学习计划；当日为空时惰性 AI 生成 3~5 项（LLM 失败降级规则模板） |
| POST / DELETE | /api/plans / /api/plans/{plan_id} | 手动添加 / 删除计划任务 |
| POST | /api/plans/{plan_id}/toggle | 勾选计划项；当日完成 ≥2/3 且今日未打卡时 streak+1 |
| GET / POST | /api/question-sets | 题集列表 / 新建题集 |
| GET / DELETE | /api/question-sets/{set_id} | 题集详情 / 删除题集（级联清理进度与错题） |
| GET | /api/question-sets/{set_id}/questions | 题集内题目 |
| POST | /api/question-sets/generate | 触发出题（三通道统一入口，body: `{source, resumeId?, settings:{count, difficulty, with_answer}}`） |
| GET | /api/question-sets/{task_id}/stream | SSE 流式出题进度（观察者，断开不影响生成） |
| GET | /api/question-sets/{task_id}/progress | 轮询兜底 |
| GET | /api/questions | 题目列表（按 setId 过滤） |
| GET | /api/questions/{question_id} | 题目详情（含全站答对率） |
| GET | /api/questions/{question_id}/site-stats | 全站答对率（低样本 <100 次作答返回 null） |
| GET | /api/progress/{set_id} | 某题集刷题进度 |
| POST | /api/progress/reset | 重置刷题进度 |
| POST | /api/practice/submit | 提交作答（判分 + 错题自动收录） |
| GET | /api/favorites | 收藏列表 |
| POST | /api/favorites/{question_id}/toggle | 收藏 / 取消收藏 |
| GET / POST | /api/wrong-book | 错题本列表 / 手动加入 |
| GET | /api/wrong-book/stats | 错题本统计（含历史已掌握底数） |
| GET | /api/wrong-book/review-queue | 到期复习队列 |
| PATCH | /api/wrong-book/{question_id}/reason | 修订错因（三分法） |
| POST | /api/wrong-book/review | 复习作答（艾宾浩斯推进 / 连对 3 次归档） |
| POST | /api/resumes | 上传简历并解析（同步 10~30s，同次产出画像 + 体检 checkup），返回 resumeId |
| GET | /api/resumes | 简历多版本列表（version 降序，各版本挂体检报告） |
| GET | /api/resumes/latest | 最新一份解析结果（出题默认用这份） |
| GET | /api/resumes/{resume_id} | 按 id 读一份版本 |
| DELETE | /api/resumes/{resume_id} | 删除一个版本（删最新版时出题底稿回退次新版） |
| GET | /api/resumes/{resume_id}/checkup | 体检报告（旧记录缺 checkup 时惰性补算写回；LLM 失败走规则兜底） |
| POST | /api/resumes/{resume_id}/optimize | AI 一键优化（重写生成新版本 version+1、is_optimized=1；失败 502） |
| GET | /api/resumes/{resume_id}/download | 导出 DOCX（python-docx，文件名 UTF-8 转义） |
| GET | /api/stats/week | 本周统计：近 7 天逐日作答柱状 + 三卡（刷题数 / 正确率 / 待复习数，answer_events 聚合） |
| GET | /api/reports/weekly | 学习周报：趋势、薄弱知识点 TOP5、AI 下周建议（按 `week-用户` 键缓存，每周仅调一次 LLM） |
| POST | /api/exams | 模考组卷：抽该题集客观题最多 40 题，限时题数×90 秒，bucket_role 取 target_role |
| GET | /api/exams / /api/exams/{exam_id} | 考试历史列表 / running 记录原样返回（前端弹「恢复上次考试」） |
| PUT | /api/exams/{exam_id}/answer | 单题作答增量落库（断网恢复基础） |
| POST | /api/exams/{exam_id}/pause / resume / abandon / submit | 暂停（累计 paused_sec）/ 恢复 / 弃考 / 交卷判分（百分制 + 维度分 + 写站内信） |
| GET | /api/exams/{exam_id}/report | 模考报告：分数 / 维度分 / 同岗位分桶百分位（样本 <5 为 null）/ 薄弱点 TOP5 / 历史趋势 |
| POST | /api/exams/{exam_id}/remedial | 按薄弱知识点标签抽同标签题生成补强练习卷（不调 LLM） |
| GET | /api/notifications | 站内信倒序列表 + 未读数；顺带惰性补写当日复习到期汇总（当日去重） |
| PUT | /api/notifications/{notification_id}/read | 单条已读 |
| PUT | /api/notifications/read-all | 全部已读；触发源：出题完成 / 模考报告 / 复习到期汇总 / 面试临近提醒 |
| GET | /api/me | 我的页一次拉齐：资料 + 三统计卡 + 我的数据计数 + 偏好 + 注销态 |
| PUT | /api/me | 更新资料（姓名 / 头像字 / 目标岗位 / 年限；改姓名未指定头像字则自动取首字） |
| PUT | /api/me/settings | 更新偏好（复习提醒开关与时间，`HH:mm` 校验） |
| DELETE | /api/me | 注销申请（写 7 天冷静期；已有申请或有进行中出题任务返回 409） |
| POST | /api/me/deactivation/cancel | 撤回注销申请（无申请 404 / 已执行 409） |
| POST | /api/me/deactivation/execute | 执行数据清理（冷静期内需 `?force=true`；已执行 409） |
| GET | /api/me/export | 数据导出：聚合该用户全部数据为单个 JSON 下载（Content-Disposition attachment，文件名 UTF-8 转义） |
| GET | /api/jobs/status | 【三期】boss-agent-cli 探测：CLI 是否安装 + Boss 登录态（前端引导卡） |
| POST | /api/jobs/search | 【三期】关键词+城市检索（CLI 子进程，stdout JSON 信封）→ 分层采样岗位卡 ≤18（薪资带三档轮取 + 单公司 ≤2）；缓存 1 天（四期增补调整，原 7 天；当日同词命中不重查，次日自动重拉最新岗位）；错误映射：登录失效 401 / 风控 423 / 超时 504 / CLI 未装 503 |
| GET | /api/jobs/{security_id}/detail | 【三期】单岗位全量 JD（job_details 缓存 7 天） |
| GET | /api/jobs/map | 【三期】岗位考点地图：TOP10 技能榜 + 高频职责 + 薪资洞察 + 三档分布（job_maps 缓存 7 天，与生成设置解耦）；未生成过 404 引导 |
| POST | /api/question-sets/generate（source=job_search） | 【三期/四期】引擎 B 出题：【四期】两阶段漏斗（Stage1 词法预筛 top-K → 仅对 K 抓 JD 详情 → Stage2 LLM 深度评分产出 0~100 匹配分，失败降级词法分）→ 聚合分析仅喂 top-M（失败降级规则版词频榜）→ 三档分批出题（必备高频 50%/加分项 30%/差异化 20%）；匹配分并入 /api/jobs/map；复用 stream/progress 与 generate_done 站内信 |
| POST | /api/question-sets/generate（source=jd_target） | 【四期增补】引擎 C（子集）单岗位专属出题（求职看板 zhipin 卡一键生成）：settings.securityId 必填（缺失 422）；题集按岗位名归并「{jobName} · 岗位专属预测题」（默认 30 题，显式 count 优先）；发起即挂接本人看板卡 setId（看板卡即刻显示该题集与进度）；后台取单份 JD（job_details 缓存 7 天，未命中节流抓取）→ 六维覆盖规划分批出题（复用引擎 A/B 复判/去重/落库骨架）；JD 不可得时任务降级 error 不污染看板；复用 stream/progress 与 generate_done 站内信 |
| GET | /api/daily-practice | 【三期】当日每日一练：首访惰性生成 10 题（薄弱 60% + 随机 40%，不调 LLM，快照当日不变） |
| POST | /api/daily-practice/progress | 【三期】标记一题完成（判分走 /api/practice/submit 原链路）；全部完成且今日未打卡 → streak+1（lastCheckinDate 同日去重） |
| GET | /api/pipeline | 【四期】求职看板四列（已投递/笔试/面试/Offer，排除回收站）+ 趋势统计（各阶段计数/本周新增/待面试/平均匹配分）；顺带惰性触发面试临近提醒 |
| POST | /api/pipeline | 【四期】加入看板：zhipin 卡（传 securityId+keyword，从检索缓存回填岗位 + 匹配分 + 挂关键词「岗位市场题集」）/ 手动卡（外部平台降级录入，无自动回填） |
| PATCH | /api/pipeline/{card_id} | 【四期】流转阶段 / 改备注 / 设面试日期 / 换挂题集（仅本人卡，否则 404） |
| DELETE | /api/pipeline/{card_id} | 【四期】移入回收站（软删 in_trash=1） |
| GET | /api/pipeline/trash | 【四期】回收站列表 |
| POST | /api/pipeline/{card_id}/restore | 【四期】从回收站还原 |
| DELETE | /api/pipeline/{card_id}/permanent | 【四期】彻底删除（永久） |
| GET | /api/health | 健康检查 |

### 管理端（需请求头 `X-Admin-Token`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/admin/llm-config | 五层模型配置（API Key 仅回显掩码） |
| PUT | /api/admin/llm-config/{layer} | 更新配置（留历史快照 + 写审计；回传掩码视为未改 Key） |
| POST | /api/admin/llm-config/{layer}/test | 连通性自测（延迟 / token / 失败原因） |
| POST | /api/admin/llm-config/{layer}/rollback | 回滚到上一版（历史耗尽返回 400） |
| GET | /api/admin/llm-config/{layer}/history | 历史快照数（前端回滚按钮可用态） |
| GET | /api/admin/audit-log | 变更审计日志（Key 只记掩码，上限 200 条；除模型发现外全部打点） |
| GET | /api/admin/providers | 供应商注册表（选定即联动 base_url，含 Key 占位 / 可承接分层） |
| POST | /api/admin/models/discover | 按供应商 + Key 拉取可用模型（`source=remote/local/static/none`，失败回落常用候选） |
| GET | /api/admin/local-models | 项目内已下载的本地模型清单（读 `models/manifest.json`） |

> 对应前端页面：`apps/web/app/admin/models/page.tsx`（路由 `/admin/models`，独立顶栏、不进 C 端导航），
> 前端已直连后端（非 Mock），**管理端页面依赖本服务运行**。
>
> 模型配置 / 历史快照 / 审计日志持久化在 `config/llm.json`（真实配置含混淆 Key，被 `.gitignore` 忽略不入仓；
> 仓内是同目录模板 `llm.json.example`，Key 全空）。启动时 llm.json 缺失会从模板复制生成；
> 每次变更后整文件原子重写（先写 .tmp 再替换）。审计除「模型发现」外全部打点（修改配置 / 连通性自测 / 版本回滚 / 停用名迁移）。
>
> `/api/me*` 对应前端页面：`apps/web/app/me/page.tsx`（路由 `/me`，由顶栏头像进入）。注销已执行后账号进入不可逆的「已注销」态，`PUT /api/me` 与 `PUT /api/me/settings` 返回 410；
> 无定时器，清理由 `POST /api/me/deactivation/execute?force=true` 手动触发以验证链路，接定时任务后改为扫描 `cooling_off_until` 到期项。
>
> 接口命名已与文档 4.5 对齐：出题统一走 `POST /api/question-sets/generate`（`source` 区分简历 / 岗位检索 / JD 定向三通道），
> 题集 CRUD 在 `/api/question-sets`，题目与全站统计在 `/api/questions`。
>
> **二期新增能力**：学习计划（`/api/plans`，惰性 AI 生成 + 打卡）、周报（`/api/reports/weekly`）、模拟考试（`/api/exams` 全套）、
> 简历多版本 + 体检 + AI 优化 + DOCX 下载（`/api/resumes*`）、站内通知（`/api/notifications`）、数据导出（`/api/me/export`）。
> 新增表 `study_plans` / `exam_records` / `notifications`，`users` 加 `last_checkin_date`，`resumes` 改多版本结构；
> 后端连接 MySQL 时 `_ensure_upgrade` 会幂等补建 / 迁移，老库可直接启动，无需手动重跑 `schema.sql`（重跑亦安全，全部 CREATE IF NOT EXISTS）。
>
> **三期新增能力（引擎 B 岗位市场 + 每日一练）**：岗位检索（`/api/jobs/*`）、引擎 B 出题通道（generate source=job_search）、
> 考点地图（`/api/jobs/map`）、每日一练（`/api/daily-practice*`）。新增表 `jobs_cache` / `job_details` / `job_maps` / `daily_practices`，
> 均由 `_ensure_upgrade` 幂等补建；三层缓存 TTL 7 天惰性过期刷新（无定时器），内存镜像 + MySQL 回源双模式。
>
> **四期新增能力（求职看板 + 引擎 B 两阶段评分漏斗）**：求职看板（`/api/pipeline*`：四列状态机 / 回收站 / 趋势统计 / 面试临近提醒）、
> 引擎 B 两阶段漏斗（生成 source=job_search 时 Stage1 词法预筛 top-K → Stage2 LLM 深度评分产出匹配分，失败降级词法分不阻断）。
> 新增表 `job_pipeline`（四列状态机，`_ensure_upgrade` 幂等补建）；面试临近提醒复用 notifications 机制（`interview_prep` 类型，当日去重，无定时器）；
> 看板为纯本地状态机，不触达平台 / 不自动投递 / 不打招呼 / 不监听 HR，状态与 HR 回复均由用户手动流转。
>
> **四期增补（单岗位专属预测题 + 检索卡岗位要求）**：引擎 C 子集落地——generate source=jd_target（看板 zhipin 卡一键生成，
> 发起即挂接卡 setId，题集按岗位名归并「{jobName} · 岗位专属预测题」，默认 30 题）；检索岗位卡支持展开「岗位要求」
> （单卡惰性拉取 `/api/jobs/{security_id}/detail`，不批量抓详情，规避限流/风控）；检索列表出参补 `description` 字段（无则为空串）。
> 无新增表，出题骨架复用引擎 A/B（六维规划 + 复判 + 去重 + SSE 进度）；单岗位出题依赖同一 Boss 环境（专用 Chrome CDP + 登录态），
> 未就绪时任务降级报错引导，看板手动流转与手动录入不受影响。

## 岗位检索环境准备（三期，引擎 B）

```powershell
# ① 安装 CLI（PyPI 镜像超时可加 --index-url https://pypi.org/simple）
uv tool install boss-agent-cli
# ② 登录用浏览器内核（CLI venv 内的 patchright，不在 PATH）
# 官方 CDN 下载慢时可加镜像：$env:PLAYWRIGHT_DOWNLOAD_HOST='https://cdn.npmmirror.com/binaries/playwright'
& "$(uv tool dir)\boss-agent-cli\Scripts\patchright.exe" install chromium
# ③ 登录 Boss 直聘（服务端运行，App 扫码；凭据加密存 ~/.boss-agent/）
boss login
```

- CLI 可执行文件位于 `~/.local/bin/`（`boss_cli.py` 自动探测 PATH 与该目录）；未安装时接口返回 503 + 安装指引。
- **合规口径**：仅调用只读检索原语 `status / search / detail`，不调 crawl / 打招呼等动作；节流由 CLI 内置高斯延迟承担，
  另有 `BossLimiter`（最小 3s 间隔）附加保险；页面固定展示「数据来自 Boss 直聘 · 实时检索 · 缓存 1 天」声明。
- **缓存 TTL 口径**（四期增补调整后）：检索列表 `store.JOBS_CACHE_TTL_DAYS = 1` 天（岗位新鲜度优先）；
  JD 详情 `JOB_DETAIL_TTL_DAYS = 7` 天（单卡惰性抓取、风控最敏感且 JD 文本变动小）；考点地图 `JOB_MAP_TTL_DAYS = 7` 天（本侧生成的报告，过期即需重新生成题库）。
  TTL 调小对存量行也立即生效：`db.load_jobs_cache` 除 `expires_at > NOW()` 外还按 `created_at` 以当前 TTL 兜底裁剪（`save_jobs_cache` 覆盖写入时刷新 `created_at`）。
  连带影响：当日未检索就点「生成市场题库」会得 409 引导先检索；隔天加入看板可能因缓存过期回填不到岗位名而得 422（重新检索即可）。

### 异常矩阵专项演练（验收门禁）

boss_cli.py 将 CLI 错误信封（`error.code / recoverable / recovery_action`）分类映射为 HTTP 状态码 + 中文恢复指引：

| 场景 | 注入方式 | 预期 | 实测结果 |
|---|---|---|---|
| 登录失效（AUTH_REQUIRED） | 未登录直接调 `POST /api/jobs/search` | 401 + detail「运行 `boss login` 扫码」+ recovery_action | ✅ 已实测：401，detail/恢复指引正确返回 |
| 风控命中（RISK/SAFETY） | 真实触发：CDP 模式下 `boss search` 返回 `ACCOUNT_RISK (code 36)` | 423 + 透传恢复指令 | ✅ **真实信封实测**：ACCOUNT_RISK→BossRiskError→423，detail/recovery 透传「停止自动化访问；回到 BOSS 直聘官方页面手动处理」 |
| 网络超时 | 注入 `subprocess.run` 抛 `TimeoutExpired` | 504 + 重试提示 | ✅ 已实测：BossTimeoutError→504，detail「boss search 执行超时（5s），请稍后重试」 |
| CLI 缺失 / 接口漂移 | 注入 `resolve_boss_exe → None` | 503 + 安装命令指引 | ✅ 已实测：run_boss→BossMissingError（503，recovery=`uv tool install boss-agent-cli`）；boss_status→`{cliInstalled:false, loggedIn:false, detail:安装指引}` |

> 401/504/503 为真实或注入实测；**423 为真实风控信封实测**（调试期间频繁自动化访问触发 zhipin code 36 账户异常，CLI 返回 recoverable=false）。
> CLI stdout 仅输出 JSON 信封，stderr 日志不混入；`boss status` 为本地读不走节流，search/detail 共享节流窗口。
>
> **运行期发现（引擎 B 与 zhipin 反爬的实际约束）**：
> 1. **headless 检索不可用（已定论，勿再尝试一次性浏览器方案）**：boss-agent-cli 2.0.0 的 headless 通道对 `zhipin.com` 首页用 `goto(wait_until='domcontentloaded')`；
>    实测带登录 `wt2` Cookie 时该事件**永不触发**（页面 0.4s 即 commit 200，但 domcontentloaded 40s 超时）——无 Cookie 时正常。
>    进一步验证：即使 monkeypatch 把该 `goto` 改成 `wait_until='commit'`（绕过 domcontentloaded 超时；headless 浏览器本身正常，`example.com` 0.8s/200），带登录态访问 zhipin 时其 API `fetch` 仍**永久挂起**。
>    根因：zhipin 反爬只向「交互式通过安全校验的真实浏览器」下发检索数据，headless 拿不到 `__zp_stoken__` 挑战解。故认证检索**本质需要一个真实浏览器会话**，纯 headless 一次性方案不可行（补丁反使 hang 从 ~15s→502 恶化为 ~90s→504，已回退，仅保留下方 gbk 编码修复）。
> 2. **需常驻真实浏览器（已落地策略：持久化专用 profile + 一键启动器 + 预检，借鉴 BossHunter）**：可用路径是保持一个已登录 zhipin 的真实 Chrome（`--remote-debugging-port=9222`），`boss search` 经 CDP 复用其已登录 context 取数（`boss login --cdp` 完成后会关闭其 Chrome，需另行常驻）。同类成熟项目 [BossHunter](https://github.com/shengjidaguai-china/BossHunter)（1.1k★）同样用 `patchright + connect_over_cdp` 复用常驻真实 Chrome，佐证「headless 不可行」是平台约束而非本项目缺陷。为把「常驻」变省心，本项目按其思路落地（`app/boss_browser.py`）：
>    - **一键启动器** `scripts/start_boss_chrome.ps1`：以**持久化专用 profile**（`--user-data-dir=%LOCALAPPDATA%\AiInterviewChrome`，与日常浏览器隔离）+ 9222 端口启动 Chrome 并打开 zhipin，轮询 `/json/version` 就绪。**登录一次**后 Cookie 存于该 profile，之后重启免重复登录。（脚本含中文，存为 UTF-8 **with BOM**，否则 Windows PowerShell 5.1 按 GBK 读取会乱码致解析失败。）
>    - **检索前 CDP 预检** `boss_browser.cdp_reachable()`：9222 未就绪时 `search/detail` 立即抛 `BossBrowserError(503)` 并给出启动指引，**不再降级 headless 长挂起**（消除 item 1 的 ~90s→504）；`BOSS_SKIP_CDP_PREFLIGHT=1` 可关闭，`BOSS_CDP_PORT`/`BOSS_CHROME_PROFILE` 可覆盖端口与 profile。
>    - **状态与按需启动**：`GET /api/jobs/status` 增返 `browserConnected/browserHint/browserStartCommand`；`POST /api/jobs/browser/start` 供前端「启动浏览器」按钮按需拉起专用 Chrome（**仅用户显式触发**，后端不自动 spawn）。
> 3. **风控敏感**：短时间内多次自动化 goto/search 会触发 zhipin 账户风控（code 36，recoverable=false），需停止自动化、回官方页面手动处理并冷却。
>    生产部署引擎 B 必须严格限频（本层 `BossLimiter` 最小 3s + CLI 内置高斯延迟），且建议常驻单一 CDP 浏览器、避免反复起停。
> 4. **Windows gbk 编码坑（已修复）**：CLI 子进程 stdout 默认走控制台 gbk(cp936)，输出薪资里的非断行连字符 U+2011（如「20‑30K」）等非 ASCII 字符时会在子进程内 `UnicodeEncodeError`，导致 JSON 信封被截断、父侧解析失败。
>    修复：`boss_cli._utf8_env()` 给子进程注入 `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`，与父侧 `encoding='utf-8'` 对齐。CDP 复用真实标签检索已实测通过（salary「11-16K·13薪」、skills、中文岗位名均正确）。
> 5. **Bridge 通道（Chrome 扩展 + 本地 daemon）＝「免专用 CDP Chrome」的设计答案，但 2.0.0 未就绪**：`browser_source` 的 `auto` 链为 bridge→cdp→headless；bridge 复用用户**日常 Chrome** 里的扩展（daemon 监听 `127.0.0.1:19826`，扩展经 WS 连接、自管 automation 窗口，无需手动常驻 zhipin 标签页），最贴近「登录一次、不常驻专用 CDP」的诉求。但实测当前安装：① `aiohttp` 未装（属 `[bridge]` extra，`daemon._run_daemon` 依赖它）；② 包内**未附带扩展**（无 `extension/` 目录、无 manifest/crx）；③ `start_daemon_background()` 已定义却**无人调用**、无 `boss bridge` 命令——即 bridge 通道在 2.0.0 处于休眠/未完成态，`_try_bridge()` 仅探测「已在运行的 daemon」，探测不到即跳过。
>    若要启用：`uv tool install "boss-agent-cli[bridge]"` 补 aiohttp + 另行获取并 `chrome://extensions` 加载扩展 + 手动起 daemon（`python -m boss_agent_cli.bridge.daemon --serve`），且仍需日常 Chrome 常驻运行——**非开箱即用**，扩展来源不确定。

> 接口回归验证可直接用 Swagger（http://localhost:8000/docs）或 curl / Postman 按本清单逐条请求；
> 变更脚本（smoke_*.ps1）已按「测试通过即删」的约定移除，不入仓。
