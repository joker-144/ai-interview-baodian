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
PostgreSQL + pgvector                 ← 题库 / 复习调度 / 向量去重
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
│   ├── api/            # FastAPI 后端（一期内存 Mock，预留 PG/pgvector；管理端配置落盘 config/llm.json）
│   │   └── models/     # 本地模型权重（入仓跟踪，可用 scripts/download_models.py 重新下载）
│   └── miniapp/        # Taro 小程序（规划中）
└── README.md
```

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
