# AI Interview Baodian（AI 智慧面试宝典）

> AI 面试训练平台：基于**你的简历**与**招聘平台真实在招 JD** 智能出题，每题附参考答案、解析与面试官追问链。

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](./LICENSE)

像做题一样系统化训练面试——海量结构化题库 + AI 动态出题 + 错题强化 + 语音模拟面试。

## 核心特性

### 三大智能出题引擎

| 引擎 | 输入 | 能力 |
|---|---|---|
| **A · 简历智能分析** | 上传简历（PDF/Word/图片/文本） | 六维覆盖矩阵（技能八股/项目深挖/场景设计/行为面试/HR 综合/压力陷阱）出题，每题可溯源简历原文，附能力维度评估与简历体检报告 |
| **B · 岗位方向检索** | 岗位关键词 + 城市 | 基于 [boss-agent-cli](https://github.com/can4hou6joeng4/boss-agent-cli)（MCP 接入）采样 15~20 个真实在招岗位，聚合市场考点，生成「必备高频 / 加分项 / 差异化」三档题库 |
| **C · JD 精准定制** | 岗位链接 / 截图 / 粘贴文本 | 抓取 JD + 公司背景，与简历交叉生成定向冲刺包，含**岗位匹配度报告**（强匹配 / 弱匹配补强 / 缺口应对话术） |

### 结构化题库训练体验

- 题库中心：来源筛选、进度条、继续刷题
- 刷题模式：专项 / 顺序 / 随机 / 背题 / 攻克难题 / 限时模拟考试
- 答题卡交互、全站答对率、解析与参考回答双栏
- **错题本**：错因三分法 + 艾宾浩斯五档复习曲线（1/2/4/7/15 天），连对 3 次毕业
- 今日学习计划、连续打卡 streak、学习周报、知识点掌握度图谱
- **语音模拟面试**：流式 ASR/TTS、AI 面试官多轮追问、STAR 四维实时评估、命中关键词检测
- 求职看板：已投递 → 笔试 → 面试 → Offer 四列状态机，面试倒计时联动冲刺

## 技术架构

```
Next.js (Web) + Taro (小程序)          ← 双端同构，同一套 API
        │ HTTPS / SSE / WebSocket
FastAPI + Celery + Redis              ← 异步任务 + 流式出题进度
        │
LangGraph 出题状态机                   ← Planner → Generator×N → Critic → Verifier → Dedup
        │                                （Checkpoint 持久化 · 崩溃续跑 · 结构化输出）
MySQL（已落地题库落库）                ← 题集 / 题目持久化；PostgreSQL + pgvector 为二期目标（复习调度 / 向量去重）
        │
本地向量模型 (apps/api/models)      ← Embedding 离线推理 · 不出网不计费
        │
boss-agent-cli (MCP)                  ← Boss 直聘只读检索 · 受控节流
```

设计要点：队列 + SSE 解决「出题慢」，状态机 + 答案二次验证解决「答案错」，MCP 只读检索解决「数据合规」，分层 LLM + 产物缓存解决「成本贵」；供应商以注册表管理（选定即联动 base_url，填 Key 后自动拉取可用模型，也可手填）；高频的向量调用走**项目内置本地模型**（权重入仓跟踪、克隆即用；需重新下载时执行 `python scripts/download_models.py`）。

## 项目结构

```
ai-interview-baodian/
├── docs/               # 设计文档（脱敏版）
├── scripts/            # 模型下载脚本（download_models.py）
├── apps/
│   ├── web/            # Next.js 14 前端（C 端页面 + /admin/models 管理端）
│   ├── api/            # FastAPI 后端（引擎 A 真实链路；题库可落 MySQL）
│   │   ├── config/     # 运行期配置：llm.json（分层模型 + 混淆 Key）、db.json（MySQL 连接）
│   │   ├── sql/        # schema.sql：题库建库脚本（手动执行一次）
│   │   └── models/     # 本地模型权重（入仓跟踪，可用 scripts/download_models.py 重新下载）
│   └── miniapp/        # Taro 小程序（规划中）
└── README.md
```

## 快速开始（首次安装）

从零到可用：装依赖 → 建库 → 起后端 → 起前端 → 配置模型。全部命令在 **PowerShell** 下执行，示例路径以仓库根目录为起点。

### 0. 环境要求

| 依赖 | 版本 | 是否必需 | 用途 |
|---|---|---|---|
| Python | 3.11+（实测 3.13.5） | 必需 | FastAPI 后端 |
| Node.js | 18+（实测 v24.14.0） | 必需 | Next.js 14 前端 |
| MySQL | 8.0（用到了 JSON 类型） | 可选 | 题库持久化；**不装也能跑但重启丢数据** |
| 云端大模型 API Key | DeepSeek / OpenAI / 通义等 | 必需 | 简历解析与出题（在管理端配置） |

### 1. 安装后端依赖

```powershell
cd apps\api
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # CMD 用 .venv\Scripts\activate.bat
python -m pip install -r requirements.txt
```

> `requirements.txt` 里注释掉了 `uvicorn[standard]`（Python 3.14 下 httptools 无预编译包），装普通 `uvicorn` 即可。
> `sentence-transformers` 会连带安装 torch，体积较大且只在本地跑 Embedding 时需要；若 Embedding 层改用云端，可先跳过它。

### 2. 下载本地向量模型（Embedding 层默认离线推理）

```powershell
cd ..\..                              # 回到仓库根目录
python scripts\download_models.py     # 默认 bge-small-zh-v1.5，约 91 MB
```

权重落在 `apps/api/models/`，且**已入仓跟踪**，正常克隆后已存在，仅在缺失时才需执行。

### 3. 初始化 MySQL 题库表（可选，推荐）

用管理工具（Navicat / DataGrip / MySQL Workbench）新建查询并整段执行 `apps/api/sql/schema.sql` 亦可；命令行方式如下（**注意 PowerShell 不支持 `<` 重定向**，故用 `-e "source ..."`）：

```powershell
mysql -u root -p --default-character-set=utf8mb4 -e "source apps/api/sql/schema.sql"
```

脚本会创建 `ai_interview_baodian` 库与 `question_sets` / `questions` 两张表。随后复制连接配置模板并填入你的密码：

```powershell
copy apps\api\config\db.json.example apps\api\config\db.json
```

然后编辑 `apps/api/config/db.json`，把 `password` 改成你的 MySQL 密码（其余字段保持默认即可）：

```json
{
  "enabled": true,
  "host": "127.0.0.1",
  "port": 3306,
  "user": "root",
  "password": "你的 MySQL 密码",
  "database": "ai_interview_baodian",
  "charset": "utf8mb4"
}
```

> 模板 `db.json.example` 入仓，真实配置 `db.json` 含明文密码、已在 `.gitignore` 中忽略，不会被提交。
> **降级行为**：不建 `db.json`、`enabled=false`、密码错误或库不存在时，后端只打印一条告警并自动退回内存模式，其余功能不受影响，只是重启后生成数据会丢失。
> 首次启动且库中为空时仍显示演示种子题集；**一旦库中有了题集，就以库内数据为准**（种子不再出现）。

### 4. 启动后端

```powershell
cd apps\api
python -m uvicorn app.main:app --port 8000        # 开发时加 --reload 可热重载
```

- 健康检查：http://127.0.0.1:8000/api/health
- 接口文档（Swagger）：http://127.0.0.1:8000/docs

### 5. 启动前端

另开一个终端：

```powershell
cd apps\web
npm install
npm run dev
```

浏览器打开 **http://localhost:3000**。前端默认直连 `http://127.0.0.1:8000`（见 `apps/web/lib/api.ts` 顶部的 `API_BASE`），后端未启动时页面会提示「无法连接后端服务」。

### 6. 配置大模型（首次必做）

1. 打开 http://localhost:3000/admin/models ；
2. 输入管理端令牌，开发态默认 **`admin-dev-token`**（对应后端环境变量 `ADMIN_TOKEN`，生产必须改）；
3. 按分层配置：`主模型 / 轻量模型 / 多模态` 填云端 API Key（如 DeepSeek，选定供应商后 `baseUrl` 会自动回填），`Embedding` 直接选「本地模型（项目内置）」，`语音` 一期可停用；
4. 每层点「测试连接」确认可通。Embedding 层的自测是**真实推理**，会校验同义句与无关句的相似度区分度。

配置落盘在 `apps/api/config/llm.json`（Key 为 XOR+base64 **混淆**存储、接口只回显掩码，该文件已在 `.gitignore` 中）。文件缺失时会自动从同目录模板 `llm.json.example` 复制生成。

### 7. 验证安装成功

打开 http://localhost:3000/resume ，上传一份**可复制文字**的简历（PDF / Word / TXT，≤10MB，扫描件无法抽取文本）：

- 解析约 10~30 秒 → 页面出现目标岗位、工作年限、能力维度评分、预估题量；
- 点「生成专属题库」→ 按 AI 预估题量（40 / 80 / 120 档）真实调用主模型出题，SSE 实时推进度；
- 完成后跳转「题库中心」，新题集可立即刷题。

用 `mysql -u root -p ai_interview_baodian -e "SELECT id, question_count, updated_at FROM question_sets;"` 可确认题目已落库，**重启后端数据仍在**。

### 常用环境变量（后端）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ADMIN_TOKEN` | `admin-dev-token` | 管理端令牌（请求头 `X-Admin-Token`），生产必须改值 |
| `KEY_SECRET` | `aib-dev-key-secret-change-me` | API Key 混淆密钥；一期为 XOR+base64（非加密），生产需换 KMS / Fernet |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | 前端来源白名单（逗号分隔） |
| `LOCAL_MODEL_THREADS` | `2` | 本地推理线程数；容器 / 低内存环境调低可避免 OpenBLAS 分配失败 |

完整清单与接口列表见 [apps/api/README.md](./apps/api/README.md)。

### 常见问题

| 现象 | 原因与处理 |
|---|---|
| 前端提示「无法连接后端服务」 | 后端未启动或端口不是 8000；确认 `python -m uvicorn app.main:app --port 8000` 已在 `apps/api` 下运行 |
| 启动日志出现「题库 MySQL 持久化不可用」 | MySQL 未启动 / 密码不对 / 未执行 `schema.sql`；按告警内容修正 `config/db.json`，改完重启后端 |
| 出题报「输出被 max_tokens 截断」 | 推理型模型（如 deepseek-flash）的思考 token 会占用输出预算；在管理端调大该分层的 `maxTokens`（本项目默认已按 8192 / 12288 预留） |
| 管理端提示未授权 / 401 | 令牌与后端 `ADMIN_TOKEN` 不一致；开发态默认 `admin-dev-token` |
| 简历解析失败、提示疑似扫描件 | 图片型 PDF 无文字层，需换可复制文字的版本（或二期接入多模态 OCR） |
| Embedding 自测失败、报内存分配错误 | 本地推理线程过多；设 `LOCAL_MODEL_THREADS=1` 后重试 |
| 重启后只看到自己生成的题集 | 正常行为：库中已有题集时以库为准，演示种子题集不再展示 |

## 合规声明

- Boss 直聘数据仅通过用户授权的低频**只读检索**获取（boss-agent-cli），不做批量采集与自动化投递；
- 简历等个人数据脱敏后才送 LLM，支持一键导出与彻底删除（账号注销入口在「我的」页：二次确认并逐条明示删除范围 → 7 天冷静期可撤回 → 到期清理不可逆）；
- AI 生成的参考答案标注「仅供参考」，客观题采用双模型交叉验证。

## Roadmap

- [ ] v0.1 基础训练闭环：账号 + 引擎 A + 刷题 + 错题本
- [ ] v0.2 学习计划 + 模拟考试 + 简历体检 / AI 一键优化
- [ ] v0.3 引擎 B（岗位检索）+ 考点地图
- [ ] v0.4 引擎 C（JD 定向）+ 求职看板
- [ ] v0.5 语音模拟面试（Web）
- [ ] v1.0 小程序端

## 许可证

本项目采用 [**GNU AGPL-3.0**](./LICENSE) 开源。

即：自由使用、修改、分发；但若将其部署为网络服务对外提供，须同样以 AGPL-3.0 开源你的修改版本。商业授权请联系仓库所有者。
