# AI 面试宝典 · API（apps/api）

FastAPI 后端骨架，对齐产品文档 4.5 的一期接口。C 端为**内存 Mock 实现**（种子数据与前端 `apps/web/lib/mock-data.ts` 一致）；**管理端模型配置已落盘**（`config/llm.json`，含混淆 Key，重启不丢）。数据库与 LLM 接入点已预留。

## 启动

```powershell
cd apps/api
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

- 接口文档（Swagger）：http://localhost:8000/docs
- 前端联调：`apps/web/lib/api.ts` 的管理端函数（模型配置 / 审计）已直连 `http://127.0.0.1:8000/api/admin/*`；C 端仍为 localStorage Mock

## 结构

```
apps/api/
├── requirements.txt
├── config/          # 管理端模型配置：llm.json.example（模板，入仓，Key 全空）+ llm.json（真实配置，.gitignore 忽略）
├── models/          # 本地模型权重目录（入仓跟踪，见下「本地模型」）+ manifest.json
└── app/
    ├── main.py        # FastAPI 入口 + CORS
    ├── config.py      # 配置（环境变量）
    ├── schemas.py     # Pydantic 模型（对齐前端 lib/types.ts）
    ├── models.py      # SQLAlchemy 一期表定义（预留，接 PostgreSQL 时启用）
    ├── store.py       # 内存数据仓库 + 种子数据（启动时从 config/llm.json 恢复模型配置）
    ├── providers.py   # LLM 供应商注册表 + 模型发现（base_url / 是否需 Key / 可承接分层）
    ├── local_models.py # 本地模型离线推理（懒加载单例 + 真实自测）
    ├── llm_config_file.py # 模型配置 / 历史快照 / 审计的落盘读写边界（原子写）
    └── routers/       # 一期接口路由
        ├── auth.py         # 登录（手机号/微信 mock）/ 我的信息
        ├── plans.py        # 今日学习计划
        ├── question_sets.py # 题集 CRUD + 出题任务（三通道统一入口、SSE 流式进度）
        ├── questions.py    # 题目查询 / 全站答对率（低样本保护）
        ├── practice.py     # 刷题进度 / 提交判分（错题自动收录）/ 收藏
        ├── wrong_book.py   # 错题本 / 艾宾浩斯复习
        ├── resumes.py      # 简历上传解析 + 能力维度评估（引擎 A 输入侧）
        ├── stats.py        # 周学习统计（柱状图 + 汇总）
        ├── me.py           # 我的 / 设置（P14）：资料 + 统计 + 复习提醒 + 注销冷静期链路
        └── admin.py        # 【管理端】分层模型配置 / 连通性自测 / 回滚 / 审计（文档 4.6.1）
```

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | 前端来源白名单（逗号分隔） |
| `MOCK_LLM` | `1` | 一期 Mock 开关：解析/出题/连通性自测走模拟实现 |
| `ADMIN_TOKEN` | `admin-dev-token` | 管理端令牌，请求头 `X-Admin-Token` 校验；生产必须改值并定期轮换 |
| `KEY_SECRET` | `aib-dev-key-secret-change-me` | API Key 存储密钥，不入仓；一期为 XOR+base64 **混淆**（非加密），生产需换 KMS / Fernet |
| `DATABASE_URL` | `postgresql+asyncpg://...` | 二期预留，当前内存实现不使用 |
| `LOCAL_MODELS_DIR` | `apps/api/models` | 本地模型权重存放目录 |
| `LOCAL_MODEL_THREADS` | `2` | 本地推理线程数；受限环境（容器 / 低内存）调低可避免 OpenBLAS 分配失败 |

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
| POST | /api/auth/phone-login | 手机号验证码登录（mock） |
| POST | /api/auth/wechat-login | 微信登录（mock） |
| GET | /api/auth/me | 当前用户信息 |
| GET / POST | /api/plans | 今日学习计划读取 / 添加 |
| POST | /api/plans/{plan_id}/toggle | 勾选计划项 |
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
| POST | /api/resumes | 上传简历并解析（mock） |
| GET | /api/resumes/latest | 最近一次解析结果（能力维度评估 + 预估题量） |
| GET | /api/resumes/{resume_id} | 按 id 读解析结果（一期仅 `resume-latest`，二期支持多份） |
| GET | /api/stats/week | 周学习统计 |
| GET | /api/me | 我的页一次拉齐：资料 + 三统计卡 + 我的数据计数 + 偏好 + 注销态 |
| PUT | /api/me | 更新资料（姓名 / 头像字 / 目标岗位 / 年限；改姓名未指定头像字则自动取首字） |
| PUT | /api/me/settings | 更新偏好（复习提醒开关与时间，`HH:mm` 校验） |
| DELETE | /api/me | 注销申请（写 7 天冷静期；已有申请或有进行中出题任务返回 409） |
| POST | /api/me/deactivation/cancel | 撤回注销申请（无申请 404 / 已执行 409） |
| POST | /api/me/deactivation/execute | 执行数据清理（冷静期内需 `?force=true`；已执行 409） |
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
> 一期无定时器，清理由 `POST /api/me/deactivation/execute?force=true` 手动触发以验证链路，接 PG 后改为定时任务扫描 `cooling_off_until` 到期项。
>
> 接口命名已与文档 4.5 对齐：出题统一走 `POST /api/question-sets/generate`（`source` 区分简历 / 岗位检索 / JD 定向三通道），
> 题集 CRUD 在 `/api/question-sets`，题目与全站统计在 `/api/questions`。
>
> 接口回归验证可直接用 Swagger（http://localhost:8000/docs）或 curl / Postman 按本清单逐条请求；
> 变更脚本（smoke_*.ps1）已按「测试通过即删」的约定移除，不入仓。
