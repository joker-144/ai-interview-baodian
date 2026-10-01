"""内存数据仓库：种子数据与前端 apps/web/lib/mock-data.ts 保持一致。

题库（题集 + 题目）在有 MySQL 时优先落库（app/db.py）：
启动时库内已有题集则以库为准；写入走「内存 + MySQL」尽力而为双写。
用户维度状态（进度 / 错题 / 收藏 / 简历 / 学习计划等）同样按用户隔离并尽力落库。
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
from threading import Lock
from typing import Any
from uuid import uuid4

from app import db, llm_config_file


def now_str() -> str:
    """题库展示时间口径：真实时间 YYYY-MM-DD HH:MM。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _seed_time(hours_ago: float) -> str:
    """种子题集的更新时间：以启动时刻为基准回推，保证展示的是真实时刻。"""
    return (datetime.now() - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M")


# ---------------- 常量 ----------------

REVIEW_STAGE_LABELS = ["今天", "第 2 天", "第 4 天", "第 7 天", "第 15 天"]
MASTERED_BASE = 0  # 已停用假底数：「已掌握」改为真实错题本聚合（保留常量兼容旧引用）

GENERATE_DIMENSIONS = ["技能八股", "项目深挖", "场景设计", "行为面试", "HR 综合", "压力/陷阱题"]

# 一期单用户 Mock：接 JWT 后由 token 解析取代（各路由统一引用本常量）
USER_ID = "demo-user"

# 注销冷静期（文档 3.7 / 第五章合规：7 天内可撤回，逾期不可恢复）
DEACTIVATION_COOLING_DAYS = 7
# 注销时需清理的数据范围（向用户明示，对齐「一键删除全部数据」承诺）
DELETION_SCOPES = [
    "个人资料与账号绑定（手机号 / 微信）",
    "题集与刷题进度、答题事件明细",
    "错题本与复习调度队列",
    "收藏与笔记",
    "简历原文件与解析产物（对象存储物理删除）",
    "学习统计与周报数据",
]

# 低样本保护：单题真实作答次数不足 100 不展示全站答对率（合规红线第 4 条）
MIN_SAMPLE = 100

# ---------------- 种子数据 ----------------

USER = {
    "name": "林晓",
    "avatarText": "林",
    "targetRole": "产品经理",
    "years": 3,
    # 进度类字段不再伪造：累计作答 / 答对率由 answer_events 真实聚合，连续打卡由真实打卡驱动
    "streak": 0,
    "totalAnswered": 0,
    "correctRate": 0,
    "phone": "138****6021",  # 脱敏回显，明文不落接口
    "wechatBound": True,
}

# 种子资料仅作用于演示账号（身份展示用）；进度 / 统计一律由真实作答事件与打卡驱动，不再伪造
DEMO_USER_ID = "demo-user"

_NEW_USER_BASE = {
    "name": "", "avatarText": "", "targetRole": "", "years": 0,
    "streak": 0, "totalAnswered": 0, "correctRate": 0,
    "phone": "", "wechatBound": False, "lastCheckinDate": "",
}

# P14 我的页偏好（一期仅复习提醒；通知管理 / 数据导出落二期）
USER_SETTINGS_SEED = {
    "reviewReminderEnabled": True,
    "reviewReminderTime": "20:00",
}

# 周图表 / 周报的作答事件留底（内存模式源；MySQL 模式直查 answer_events，不依赖它）
# 每次提交 append 一条，只保留最近 5000 条防内存无界增长
ANSWER_LOG_CAP = 5000

SETS_SEED = [
    {"id": "set-1", "source": "resume", "title": "产品经理核心题集", "questionCount": 6, "updatedAt": _seed_time(2)},
    {"id": "set-2", "source": "jd_target", "title": "字节跳动 · 后端开发", "questionCount": 6, "updatedAt": _seed_time(24)},
    {"id": "set-3", "source": "job_search", "title": "数据分析师高频题", "questionCount": 4, "updatedAt": _seed_time(72)},
    {"id": "set-4", "source": "resume", "title": "前端工程师全攻略", "questionCount": 3, "updatedAt": _seed_time(72)},
    {"id": "set-5", "source": "mock_interview", "title": "行为面 STAR 专场", "questionCount": 3, "updatedAt": _seed_time(168)},
    {"id": "set-6", "source": "jd_target", "title": "腾讯 · 产品经理校招", "questionCount": 3, "updatedAt": _seed_time(168)},
    {"id": "set-7", "source": "job_search", "title": "Java 后端进阶", "questionCount": 3, "updatedAt": _seed_time(336)},
    {"id": "set-8", "source": "resume", "title": "运营岗通用题集", "questionCount": 2, "updatedAt": _seed_time(336)},
    {"id": "set-9", "source": "jd_target", "title": "阿里 · 数据产品经理", "questionCount": 2, "updatedAt": _seed_time(504)},
]


def _q(
    qid: str,
    set_id: str,
    category: str,
    stem: str,
    options: list[str],
    answer: str,
    explanation: str,
    reference: str,
    tags: list[str],
    rate: int | None,
    difficulty: int = 2,
) -> dict[str, Any]:
    return {
        "id": qid,
        "setId": set_id,
        "type": "single_choice",
        "category": category,
        "stem": stem,
        "options": options,
        "answer": answer,
        "explanation": explanation,
        "referenceAnswer": reference,
        "knowledgeTags": tags,
        "difficulty": difficulty,
        "siteCorrectRate": rate,
    }


BASE_QUESTIONS: list[dict[str, Any]] = [
    # ---- set-1 产品经理核心题集 ----
    _q(
        "q-p1", "set-1", "专业技术",
        "在制定产品 roadmap 时，以下哪种优先级评估方法最适合资源有限的初创团队？",
        ["RICE 评分模型", "MoSCoW 法则", "KANO 模型分类", "SWOT 分析"], "B",
        "初创团队资源有限，MoSCoW 通过 Must/Should/Could/Won't 四级划分，能在需求远超资源时快速砍掉非核心项；RICE 需要较准确的量化预估数据，初创期数据不足时误差大。",
        "先说明选择理由（资源约束 → 快速分层），再结合一个真实取舍案例，最后补充团队对齐机制（每周 roadmap 评审）。",
        ["roadmap", "优先级", "MoSCoW"], 62,
    ),
    _q(
        "q-p3", "set-1", "专业技术",
        "一份高质量的 PRD 最应该优先保证下列哪一点？",
        ["交互细节覆盖所有极端分支", "需求背景、目标与衡量指标清晰", "UI 稿像素级标注完整", "技术实现方案详尽"], "B",
        "PRD 的第一要务是让团队理解「为什么做、做成什么样算成功」。背景、目标与衡量指标缺失时，再细的交互稿也会让研发在实现中反复返工。",
        "作答框架：先给结论（目标导向），再展开 PRD 结构（背景/目标/范围/规则/指标），最后举一例说明目标不清导致的返工。",
        ["PRD", "需求文档"], 71,
    ),
    _q(
        "q-p4", "set-1", "专业技术",
        "想验证「新用户是否在 7 天内体会到产品核心价值」，以下哪种方法最直接？",
        ["问卷调研 NPS", "用户访谈", "新手期行为漏斗与留存分析", "竞品分析"], "C",
        "「体会到核心价值」是行为问题而非态度问题，应看新用户关键行为（aha moment）的完成率与 7 日留存的关联；问卷和访谈适合做补充定性验证。",
        "先定义「核心价值行为」，再看新用户 7 天内完成该行为的比例及与留存的相关性，必要时用访谈解释行为背后的原因。",
        ["用户调研", "留存", "aha moment"], 58,
    ),
    _q(
        "q-p5", "set-1", "专业技术",
        "关于北极星指标，以下哪项理解是正确的？",
        ["北极星指标就是 DAU，适用于所有产品", "北极星指标应能反映用户获得的核心价值，并可拆解到团队行动", "北极星指标越多越好，便于全面监控", "北极星指标一旦确定就不能调整"], "B",
        "北极星指标的本质是「用户价值 × 商业价值」的交汇点，需要可拆解、可行动。不同产品/阶段北极星不同，也会随战略演进调整。",
        "定义 → 举例（如内容产品用「有效消费时长」而非 DAU）→ 说明如何拆解为输入指标分配到团队。",
        ["北极星指标", "指标体系"], 66,
    ),
    _q(
        "q-p6", "set-1", "项目深挖",
        "你负责的功能上线后核心指标不涨反跌，第一步应该做什么？",
        ["立刻回滚版本", "拆分漏斗定位下跌环节，区分口径问题与真实下跌", "加大投放拉新稀释跌幅", "等待一周再观察"], "B",
        "先定位再决策：检查数据口径是否变化、按漏斗/人群/渠道拆分定位下跌环节，确认是真实下跌后再决定回滚或修复。盲目回滚可能误伤，盲目等待会扩大损失。",
        "STAR 框架：情境（指标下跌）→ 任务（快速定位）→ 行动（口径校验 + 维度下钻 + 分群对比）→ 结果（定位原因并量化挽回）。",
        ["数据分析", "指标异动"], 74,
    ),
    _q(
        "q-p7", "set-1", "专业技术",
        "MVP（最小可行产品）的核心目的是什么？",
        ["用最低成本做出功能最全的版本", "用最小成本验证核心价值假设是否成立", "快速上线抢占市场，后续再补质量", "给投资人演示的演示版本"], "B",
        "MVP 服务于「验证假设」而非「交付功能」：用最小代价测试最关键的不确定性（用户是否真有此痛点、方案是否被接受），验证失败则低成本转向。",
        "先给定义，再举一个 MVP 形态例子（如人工兜底替代自动化），最后说明验证指标与下一步决策规则。",
        ["MVP", "精益创业"], 80,
    ),
    # ---- set-2 字节跳动 · 后端开发 ----
    _q(
        "q-t1", "set-2", "专业技术",
        "MySQL 索引失效的场景中，以下哪一项描述是不正确的？",
        ["对索引列使用函数会导致索引失效", "LIKE 以 % 开头会导致索引失效", "联合索引不满足最左前缀可能失效", "使用覆盖索引一定会导致索引失效"], "D",
        "覆盖索引是优化手段而非失效场景：查询列都被索引覆盖时无需回表，性能更好。A/B/C 均为常见失效场景。",
        "先指出 D 错误并解释覆盖索引原理，再系统列举失效场景（函数、隐式转换、前导通配、最左前缀、OR 连接非索引列等），最后提一句用 EXPLAIN 验证。",
        ["MySQL", "索引", "覆盖索引"], 55,
    ),
    _q(
        "q-t2", "set-2", "专业技术",
        "缓存穿透指的是请求打到数据库的直接原因是？",
        ["热点 key 过期瞬间被并发访问", "查询了缓存和数据库中都不存在的数据", "缓存整体宕机", "缓存数据与数据库不一致"], "B",
        "穿透 = 查「不存在」的数据，缓存永远 miss 直达 DB，常用布隆过滤器或空值缓存拦截。A 是击穿（热点 key），C 是雪崩。",
        "先辨析穿透/击穿/雪崩三兄弟，再给穿透的两种解法（布隆过滤器、空值短 TTL 缓存）及各自适用场景。",
        ["Redis", "缓存穿透", "布隆过滤器"], 69,
    ),
    _q(
        "q-t3", "set-2", "专业技术",
        "用 Redis 实现分布式锁时，下列哪组实践最安全？",
        ["SETNX 之后单独再 EXPIRE", "SET key value NX PX + 唯一 value + Lua 脚本释放", "GETSET 循环重试", "直接用 INCR 计数"], "B",
        "加锁与过期必须原子（SET NX PX 一条命令）；value 存唯一标识防止误删他人锁，释放时用 Lua 保证「比对 + 删除」原子性。A 两步非原子，宕机会死锁。",
        "按「原子加锁 → 唯一标识 → Lua 释放 → 看门狗续期（Redisson）」逐层递进，并说明各层解决的故障场景。",
        ["Redis", "分布式锁"], 63,
    ),
    _q(
        "q-t4", "set-2", "专业技术",
        "MySQL InnoDB 默认的事务隔离级别是？",
        ["读未提交", "读已提交", "可重复读", "串行化"], "C",
        "InnoDB 默认 REPEATABLE READ（可重复读），通过 MVCC + Next-Key Lock 在该级别下同时解决了幻读问题，这是与标准 SQL 定义的差异点。",
        "先答「可重复读」，再展开四个隔离级别对照表，最后点出 InnoDB 用 Next-Key Lock 防幻读这一高频追问。",
        ["MySQL", "事务", "MVCC"], 77, 1,
    ),
    _q(
        "q-t5", "set-2", "系统设计",
        "设计秒杀系统时，减库存操作最合适的做法是？",
        ["请求直接打到数据库行锁扣减", "Redis 预减库存 + MQ 异步落库", "前端按钮置灰即可", "定时任务每分钟批量扣减"], "B",
        "秒杀核心是「限流削峰 + 最终一致」：Redis 原子预减扛住瞬时并发，MQ 异步落库削峰，配合令牌限流与防重。直接打 DB 行锁在十万级 QPS 下会瞬间打满连接。",
        "按链路讲：接入层限流 → Redis 预减（Lua 原子）→ MQ 削峰 → DB 最终一致 → 超卖/少卖的兜底对账。",
        ["秒杀", "高并发", "MQ"], 70,
    ),
    _q(
        "q-t6", "set-2", "专业技术",
        "消息队列中防止消息丢失，下列哪项不是必要措施？",
        ["生产者开启发送确认（ACK）机制", "Broker 端消息持久化", "消费者处理成功后再手动 ACK", "消费者开启自动 ACK 提升吞吐"], "D",
        "自动 ACK 在消息收到即确认，处理失败就丢消息，恰恰是防丢失要避免的。A/B/C 分别覆盖生产、存储、消费三段的可靠性。",
        "按「生产者 → Broker → 消费者」三段模型分别给出可靠性措施，再点明自动 ACK 的风险与手动 ACK + 重试/死信队列的组合。",
        ["MQ", "可靠性", "ACK"], 68,
    ),
    # ---- set-3 数据分析师高频题 ----
    _q(
        "q-d1", "set-3", "专业技术",
        "A/B 测试中，样本量不足会导致哪类统计风险显著上升？",
        ["第一类错误（假阳性）", "第二类错误（假阴性），检验功效不足", "指标口径漂移", "分流不均"], "B",
        "样本量不足时统计功效（Power）下降，真实存在的差异也检不出来，即第二类错误（假阴性）风险上升；因此实验前要做样本量预估（MDE/功效分析）。",
        "先给结论，再解释 α/β/Power 的关系，最后给出实践做法：实验前按 MDE 反推样本量，不足则不轻易下「无差异」结论。",
        ["A/B 测试", "假设检验", "统计功效"], 48,
    ),
    _q(
        "q-d2", "set-3", "专业技术",
        "「各渠道转化率都在上升，但整体转化率却下降」最可能是什么现象？",
        ["数据埋点丢失", "辛普森悖论（渠道结构占比变化）", "指标计算口径错误", "季节性波动"], "B",
        "辛普森悖论：分组趋势与整体趋势相反，根因是各组样本占比变化。例如低转化渠道流量占比上升，即使各渠道自身转化都在涨，整体也会被拉低。",
        "定义辛普森悖论 → 构造数字例子现场演算 → 给出应对：分析时固定结构（分层加权）看趋势。",
        ["辛普森悖论", "转化率"], 61,
    ),
    _q(
        "q-d3", "set-3", "专业技术",
        "次日留存率的正确计算口径是？",
        ["第 2 天活跃 ÷ 第 1 天活跃", "某日新增用户中第 2 天仍活跃的人数 ÷ 该日新增用户数", "第 2 天新增 ÷ 第 1 天新增", "次日启动次数 ÷ 当日启动次数"], "B",
        "留存必须按「同期群（cohort）」计算：某天新增的用户群体里，第 2 天回来活跃的比例。A 混入了非当日新增的老用户，口径错误。",
        "给出公式并强调 cohort 概念，再延伸到 7 日/30 日留存与留存曲线形态（衰减趋平）。",
        ["留存率", "cohort"], 82, 1,
    ),
    _q(
        "q-d4", "set-3", "专业技术",
        "要计算「每个部门薪资排名第 2 高的员工」，最合适的 SQL 写法是？",
        ["GROUP BY + MAX 两次", "ROW_NUMBER() OVER (PARTITION BY 部门 ORDER BY 薪资 DESC) 取 rn=2", "LIMIT 2 OFFSET 1", "ORDER BY + HAVING"], "B",
        "分组内取 Top N 是窗口函数的经典场景：ROW_NUMBER 按部门分区排序后过滤 rn=2。GROUP BY 无法保留行级信息，LIMIT 不区分分组。",
        "写出完整 SQL，说明 PARTITION BY 与 ORDER BY 语义，并对比 RANK/DENSE_RANK 在并列场景的差异（高频追问）。",
        ["SQL", "窗口函数"], 73,
    ),
    # ---- set-4 前端工程师全攻略 ----
    _q(
        "q-f1", "set-4", "专业技术",
        "关于浏览器关键渲染路径，下列哪项会阻塞首次渲染？",
        ["带有 async 属性的外链脚本", "CSSOM 未构建完成", "懒加载的图片", "带有 defer 属性的脚本"], "B",
        "Render Tree 由 DOM + CSSOM 合并而成，CSS 是渲染阻塞资源，CSSOM 未完成前页面无法绘制。async/defer 脚本不阻塞解析，懒加载图片同理。",
        "画出 DOM/CSSOM/Render Tree/Layout/Paint 流程 → 指出阻塞点 → 给优化手段（关键 CSS 内联、媒体查询拆分、defer 脚本）。",
        ["浏览器原理", "性能优化"], 59,
    ),
    _q(
        "q-f2", "set-4", "专业技术",
        "关于 JavaScript 闭包，下列说法正确的是？",
        ["闭包会导致变量提升失效", "闭包是函数与其词法作用域的引用组合，可延长局部变量生命周期", "闭包只能在全局作用域创建", "闭包中的变量会被垃圾回收立即释放"], "B",
        "闭包 = 函数 + 其创建时的词法作用域。内层函数持有外层变量引用，使其不被回收，因此可用于私有化、柯里化，但滥用会造成内存驻留。",
        "定义 → 计数器示例 → 常见用途（防抖节流、私有变量）→ 内存注意事项（不再使用时解除引用）。",
        ["JavaScript", "闭包"], 76, 1,
    ),
    _q(
        "q-f3", "set-4", "专业技术",
        "React 中 setState 被设计为异步批量更新，主要目的是？",
        ["避免竞态条件", "合并多次更新减少渲染次数，提升性能", "为了兼容服务端渲染", "语法设计的历史遗留"], "B",
        "同一事件循环中的多次 setState 会被合并成一次渲染，避免连续 DOM 操作带来的性能损耗；这也是「setState 后立即读取 state 拿到旧值」的原因。",
        "先答性能合并，再演示批量更新示例，最后给出需要拿到最新值的正确姿势（函数式更新 / useEffect 依赖）。",
        ["React", "setState"], 64,
    ),
    # ---- set-5 行为面 STAR 专场 ----
    _q(
        "q-b1", "set-5", "行为面试",
        "面试官问「讲一次你和研发发生严重分歧的经历」，以下哪种回答结构最佳？",
        ["强调自己最终说服了对方", "STAR 结构，重点放在行动中的沟通策略与最终业务结果", "回避冲突，说团队合作一直很顺利", "详细描述对方的问题所在"], "B",
        "行为题考察的是冲突中的协作能力而非输赢。STAR 中 Action 要突出换位沟通、数据佐证、共同目标对齐，Result 落到业务结果与关系沉淀。",
        "S：背景与分歧点 → T：你的职责 → A：先理解对方约束 → 用数据/原型对齐 → 折中方案 → R：按期上线 + 指标结果 + 后续协作机制。",
        ["行为面试", "STAR", "冲突处理"], 84,
    ),
    _q(
        "q-b2", "set-5", "行为面试",
        "被问「讲一次失败经历」时，下列哪种做法最减分？",
        ["选择真实且与你直接相关的失败", "重点讲复盘方法与后续改进验证", "把失败归因于客观环境与他人", "量化说明改进后的结果"], "C",
        "失败题考察自我认知与成长型思维。甩锅式回答直接暴露低自省；高分回答是「真实失败 + 深度归因 + 可验证的改进」。",
        "选中等规模真实失败 → 归因到自身决策盲区 → 复盘方法论（如建立 checklist/数据验证机制）→ 后续项目验证改进有效。",
        ["行为面试", "失败经历"], 79,
    ),
    _q(
        "q-b3", "set-5", "行为面试",
        "面试官追问「你刚才说的项目成果里，你个人的贡献占比是多少？」，最佳应对是？",
        ["谦虚表示都是团队的功劳", "给出具体可验证的个人动作与决策点", "强调自己是项目核心，其他人配合", "转移话题到项目整体成绩"], "B",
        "追问意图是辨别「搭车者」。应具体拆解自己独立完成的决策与动作（如方案选型、关键谈判、数据分析），既不过谦也不贪功，并给出可背调的验证点。",
        "拆解项目分工 → 明确个人独立负责的 2~3 个关键决策 → 说明这些决策对结果的因果贡献 → 提供可验证方式（文档/数据/引荐人）。",
        ["行为面试", "追问应对"], 72,
    ),
    # ---- set-6 腾讯 · 产品经理校招 ----
    _q(
        "q-p2", "set-6", "专业技术",
        "以下哪一项不属于 KANO 模型中的需求分类？",
        ["基本型需求", "期望型需求", "兴奋型需求", "流量型需求"], "D",
        "KANO 模型五类：基本型、期望型、兴奋型、无差异型、反向型。「流量型需求」是运营概念，不属于 KANO 分类。",
        "列出 KANO 五分类并各举一例（如 IM 产品：基本型=消息必达，期望型=传输速度，兴奋型=好玩的彩蛋），再说明如何通过 KANO 问卷判定分类。",
        ["KANO", "需求分析"], 57,
    ),
    _q(
        "q-p8", "set-6", "专业技术",
        "校招产品面试常问「估算一个城市的网约车日订单量」，这类估算题的核心考察点是？",
        ["记住准确的行业数据", "结构化拆解问题的能力与清晰的假设链条", "心算速度", "对该公司业务的了解"], "B",
        "费米估算题考察拆解框架：人口 → 有出行需求人群 → 出行频次 → 网约车渗透率 → 订单量。数字本身不重要，假设合理、链条完整、会校验才是关键。",
        "先搭拆解树 → 逐层给出假设与依据 → 算出量级 → 用常识交叉校验（如与公开订单量级比对）。",
        ["费米估算", "结构化思维"], 67,
    ),
    _q(
        "q-p9", "set-6", "公司认知",
        "被问「你为什么想来做 C 端产品而不是 B 端？」，哪种回答更有说服力？",
        ["C 端用户量大，更有成就感", "结合自身经历说明对 C 端用户洞察的热情与方法论匹配", "B 端太复杂，不想做", "听从导师建议"], "B",
        "动机题考察自我认知与岗位匹配。要把选择锚定到「个人经历/能力模型与岗位特性的匹配」，而非泛泛的比较或被动选择。",
        "个人经历（如校园产品/社团运营中的用户洞察案例）→ 提炼出的 C 端方法论（同理心、数据敏感）→ 与该岗位业务方向的结合点。",
        ["动机题", "岗位匹配"], 75,
    ),
]


def _clone_to(q: dict[str, Any], set_id: str, suffix: str) -> dict[str, Any]:
    copied = dict(q)
    copied["id"] = f"{q['id']}{suffix}"
    copied["setId"] = set_id
    return copied


_BY_ID = {q["id"]: q for q in BASE_QUESTIONS}

QUESTIONS_SEED: list[dict[str, Any]] = [
    *BASE_QUESTIONS,
    _clone_to(_BY_ID["q-t2"], "set-7", "-s7"),
    _clone_to(_BY_ID["q-t3"], "set-7", "-s7"),
    _clone_to(_BY_ID["q-t5"], "set-7", "-s7"),
    _clone_to(_BY_ID["q-p4"], "set-8", "-s8"),
    _clone_to(_BY_ID["q-p5"], "set-8", "-s8"),
    _clone_to(_BY_ID["q-d1"], "set-9", "-s9"),
    _clone_to(_BY_ID["q-d2"], "set-9", "-s9"),
]

RESUME_ANALYSIS_SEED = {
    "fileName": "林晓-产品经理-简历.pdf",
    "years": 3,
    "targetRole": "产品经理",
    "estimatedCount": 120,
    "dimensions": [
        {"label": "项目管理", "score": 85},
        {"label": "数据分析", "score": 72},
        {"label": "沟通协作", "score": 90},
        {"label": "行业认知", "score": 68},
    ],
}


# ---------------- 管理端模型配置（文档 4.6.1） ----------------

# 四类分层模型 + 语音，对应 4.1 架构图 LLM 层
LLM_LAYERS: list[dict[str, str]] = [
    {"layer": "primary", "label": "主模型", "usage": "出题 / 简历体检 / 一键优化"},
    {"layer": "light", "label": "轻量模型", "usage": "答案二次校验 / 结构化解析 / 去重判定"},
    {"layer": "vision", "label": "多模态模型", "usage": "JD 截图解析 / 简历扫描件 OCR"},
    {"layer": "embedding", "label": "Embedding", "usage": "题目去重 / 知识点检索"},
    {"layer": "voice", "label": "语音合成（TTS）", "usage": "模拟面试·面试官读题（五期）"},
]

LLM_CONFIG_SEED: dict[str, dict[str, Any]] = {
    "primary": {
        "provider": "openai",
        "modelName": "gpt-4o",
        "apiKey": "",  # 密文存储，接口只回显掩码
        "baseUrl": "https://api.openai.com/v1",
        "params": {"temperature": 0.4, "maxTokens": 4096, "timeoutSec": 60, "concurrency": 4},
        "fallbackModel": "",
        "enabled": True,
    },
    "light": {
        "provider": "openai",
        "modelName": "gpt-4o-mini",
        "apiKey": "",
        "baseUrl": "https://api.openai.com/v1",
        "params": {"temperature": 0.0, "maxTokens": 1024, "timeoutSec": 30, "concurrency": 8},
        "fallbackModel": "",
        "enabled": True,
    },
    "vision": {
        "provider": "openai",
        "modelName": "gpt-4o",
        "apiKey": "",
        "baseUrl": "https://api.openai.com/v1",
        "params": {"temperature": 0.2, "maxTokens": 2048, "timeoutSec": 60, "concurrency": 2},
        "fallbackModel": "",
        "enabled": True,
    },
    "embedding": {
        # 默认走项目内置本地模型（apps/api/models/，离线推理、零 API 成本）；
        # 权重用 scripts/download_models.py 下载，运行时由 app/local_models.py 直接加载。
        # 需云端向量时在管理端把供应商切回 openai / qwen / siliconflow 即可。
        "provider": "local",
        "modelName": "bge-small-zh-v1.5",
        "apiKey": "",  # 本地模型无需 Key
        "baseUrl": "",  # 本地模型无 base_url
        # dimensions 变更需重建 pgvector 索引（管理端会提示）；本地模型维度由权重决定
        "params": {"dimensions": 512, "timeoutSec": 30, "concurrency": 8},
        "fallbackModel": "",
        "enabled": True,
    },
    "voice": {
        # 五期语音面试：题目文字→面试官语音，锁定云端 Qwen3-TTS-Flash（DashScope 原生 api/v1，
        # 注意与 qwen 文本层的 compatible-mode/v1 不同）；中英混排/数字/% 朗读远好于本地 Kokoro。
        # ASR（回答→文字）走本地 SenseVoice，不经此分层（见 app/asr.py，无需 Key）。
        "provider": "qwen",
        "modelName": "qwen3-tts-flash",
        "apiKey": "",
        "baseUrl": "https://dashscope.aliyuncs.com/api/v1",
        # ttsVoice=Andre（安德雷·沉稳磁性男声，管理端可切）；languageType 与题目语种一致发音更准
        "params": {"ttsVoice": "Andre", "languageType": "Chinese", "speed": 1.0, "sampleRate": 24000, "timeoutSec": 30},
        "fallbackModel": "",
        "enabled": True,
    },
}


# ---------------- 运行时状态（单进程内存；多 worker 需换 Redis/DB） ----------------


@dataclass
class GenerateTask:
    task_id: str
    total: int
    user_id: str = ""  # 发起出题的用户（JWT）；简历上下文按此读取
    generated: int = 0
    dropped: int = 0  # 被结构校验 / 答案二次校验 / 去重淘汰的题量（质量口径）
    dimension_index: int = 0
    done: bool = False
    error: str | None = None  # 失败原因（SSE / 轮询都会回传，前端提示用）
    set_id: str | None = None  # 本次生成落库的目标题集
    interrupted: bool = False  # 服务重启导致的中断任务（启动恢复置位，前端提示「继续补齐」）
    created_at: str = field(  # 秒级时间戳：active 任务按此取最新（now_str 分钟级不够分）
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    lock: Lock = field(default_factory=Lock)
    asyncio_handle: object | None = None


@dataclass
class RuntimeState:
    # user_id -> {"YYYY-MM-DD" -> list[PlanTask dict]}：当日惰性生成 / 手动维护（二期）
    plans: dict[str, dict[str, list[dict[str, Any]]]] = field(default_factory=dict)
    # 近期作答事件留底（周图表 / 周报的内存模式源；MySQL 模式直查 answer_events）
    answer_log: list[dict[str, Any]] = field(default_factory=list)
    # 题集（支持新建/删除，种子来自 SETS_SEED）
    sets: list[dict[str, Any]] = field(default_factory=lambda: [dict(s) for s in SETS_SEED])
    # user_id -> {set_id -> {question_id -> choice}}
    progress: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)
    # user_id -> list[WrongItem dict]
    wrong_book: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # user_id -> list[question_id]
    favorites: dict[str, list[str]] = field(default_factory=dict)
    # 运行时题库：种子 + 引擎生成（一期内存态，重启回到种子）
    questions: list[dict[str, Any]] = field(
        default_factory=lambda: [dict(q) for q in QUESTIONS_SEED]
    )
    generate_tasks: dict[str, GenerateTask] = field(default_factory=dict)
    # question_id -> {attempts, correct}：真实作答事件聚合（全站答对率统计源，
    # 达标 ≥ MIN_SAMPLE 时回写题目 siteCorrectRate；启动时从 answer_events 重建）
    answer_stats: dict[str, dict[str, int]] = field(default_factory=dict)
    # layer -> 配置（深拷贝自 LLM_CONFIG_SEED；存在 config/llm.json 时以其覆盖）
    llm_config: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            layer: {**cfg, "params": dict(cfg["params"])} for layer, cfg in LLM_CONFIG_SEED.items()
        }
    )
    # layer -> 历史版本快照（可回滚）
    llm_history: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # 管理端审计日志（Key 只记掩码）
    audit_log: list[dict[str, Any]] = field(default_factory=list)
    # 用户维度状态一律按 user_id 分字典（JWT 多用户），每个用户懒加载（见 ensure_user）
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)  # USER 种子的覆盖项
    settings_map: dict[str, dict[str, Any]] = field(default_factory=dict)
    deactivations: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    # user_id -> {analysis, text, summary} | None（最近一份简历解析产物）
    resumes: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    # user_id -> [简历版本行]：多版本简历（批 3，db.resumes v2 表同构，见 add_resume_version）
    resume_versions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # exam_id -> exam dict：模拟考试（内存态与 MySQL exam_records 同构，见 db._exam_row）
    exams: dict[str, dict[str, Any]] = field(default_factory=dict)
    # account -> {userId, passwordHash}：账号内存兜底（MySQL 不可用时注册/登录仍可用）
    accounts: dict[str, dict[str, str]] = field(default_factory=dict)
    # user_id -> [通知行]：站内信（内存倒序、最新在前，与 db.notifications 同构，见 add_notification）
    notifications: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # 岗位检索缓存（三期）：cache_key -> {keyword, city, payload, expiresAt}（与 db.jobs_cache 同构，内存模式源）
    jobs_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    # JD 详情缓存（三期）：security_id -> payload（与 db.job_details 同构）
    job_details: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 考点地图报告缓存（三期）：map_key -> {payload, expiresAt}（与 db.job_maps 同构）
    job_maps: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 每日一练（三期）：user_id -> {date, id, questionIds, doneIds}（与 db.daily_practices 同构）
    daily_practices: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 求职看板（四期）：user_id -> list[看板卡 dict]（与 db.job_pipeline 同构，内存镜像 + MySQL 双写）
    job_pipeline: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # 语音模拟面试（五期）：session_id -> 会话 dict（活跃会话内存态，与 db.interview_sessions 同构；
    # 会话内嵌 answers 列表供内存模式兜底，作答/追问逐行双写 db）
    interview_sessions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """模型配置 / 历史 / 审计从 config/llm.json 恢复（不存在时由模板复制或走种子）。

        以种子为底、文件值覆盖：新增分层 / 字段时旧配置文件自动获得种子默认值；
        文件损坏或缺失时 load() 返回 None，保持种子态。
        """
        persisted = llm_config_file.load()
        if persisted:
            for layer, saved in persisted["configs"].items():
                seed = self.llm_config.get(layer)
                if seed is None or not isinstance(saved, dict):
                    continue
                self.llm_config[layer] = {
                    **seed,
                    **saved,
                    "params": {**seed["params"], **saved.get("params", {})},
                }
            # voice 层迁移：旧默认种子（openai/whisper-1 且未填 Key）从未被用户自定义，
            # 自动升级到云端 Qwen3-TTS-Flash；已填 Key 或改过供应商/模型的配置保持不动。
            voice = self.llm_config.get("voice")
            if (
                voice
                and voice.get("provider") == "openai"
                and voice.get("modelName") == "whisper-1"
                and not voice.get("apiKey")
            ):
                seed_voice = LLM_CONFIG_SEED["voice"]
                self.llm_config["voice"] = {**seed_voice, "params": dict(seed_voice["params"])}
            if isinstance(persisted.get("history"), dict):
                self.llm_history = persisted["history"]
            if isinstance(persisted.get("audit"), list):
                self.audit_log = persisted["audit"]

        # 题库持久化：MySQL 可用且已有题集记录时，以库内数据为准（种子不再生效）
        bank = db.load_bank()
        if bank:
            sets, questions = bank
            self.sets = sets
            self.questions = questions

        # 全站答对率：从作答事件明细重建聚合，样本量达标的题以真实口径覆盖展示值
        for row in db.load_answer_stats():
            stats = {"attempts": row["attempts"], "correct": row["correct"]}
            self.answer_stats[row["questionId"]] = stats
            if stats["attempts"] >= MIN_SAMPLE:
                question = next(
                    (q for q in self.questions if q["id"] == row["questionId"]), None
                )
                if question:
                    question["siteCorrectRate"] = round(
                        stats["correct"] * 100 / stats["attempts"]
                    )

        # 演示账号种子（demo / demo1234，幂等）：账号体系开箱可用的最低保障，
        # 库不可用时静默跳过（ensure_user 会用内存模板兜底）
        from app.auth import hash_password  # 函数内导入，避免 store ←→ auth 循环依赖

        db.seed_demo_account(USER_ID, "demo", hash_password("demo1234"), USER)

        # 出题任务中断恢复：running 行标记为 interrupted 并在内存重建只读记录，
        # 前端据此提示「继续补齐剩余题目」；目标题集已被删除的任务无恢复意义，跳过
        for row in db.load_interrupted_generate_tasks():
            if not any(s["id"] == row["setId"] for s in self.sets):
                continue
            self.generate_tasks[row["taskId"]] = GenerateTask(
                task_id=row["taskId"],
                total=row["total"],
                user_id=row["userId"],
                generated=row["generated"],
                dropped=row["dropped"],
                done=True,
                interrupted=True,
                error="生成中断：服务重启导致任务未完成，可继续补齐剩余题目",
                set_id=row["setId"],
            )


state = RuntimeState()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


# ---------------- 运行时题库访问 ----------------
# 所有题目查询统一走这里：种子题与引擎生成的题在同一列表，路由不再直接读 QUESTIONS_SEED


def all_questions() -> list[dict[str, Any]]:
    return state.questions


def find_question(question_id: str) -> dict[str, Any] | None:
    return next((q for q in state.questions if q["id"] == question_id), None)


# ---------------- 题库归属隔离（ownerId 空=公共题库/预置种子，非空=私有） ----------------


def find_set(set_id: str) -> dict[str, Any] | None:
    return next((s for s in state.sets if s["id"] == set_id), None)


def is_public_set(s: dict[str, Any]) -> bool:
    """ownerId 为空即公共题库（预置种子），对所有用户可见。"""
    return not s.get("ownerId")


def visible_sets(user_id: str) -> list[dict[str, Any]]:
    """用户可见题集：公共题库 + 自己生成的私有题集。"""
    return [s for s in state.sets if is_public_set(s) or s.get("ownerId") == user_id]


def can_access_set(set_id: str, user_id: str) -> bool:
    """题集访问鉴权：不存在 / 他人私有题集均返回 False（不泄露存在性）。"""
    s = find_set(set_id)
    return s is not None and (is_public_set(s) or s.get("ownerId") == user_id)


def visible_questions(user_id: str) -> list[dict[str, Any]]:
    """用户可见题目：仅来自其可见题集（每日一练等随机抽题的隔离口径）。"""
    visible_ids = {s["id"] for s in visible_sets(user_id)}
    return [q for q in state.questions if q["setId"] in visible_ids]


def add_questions(items: list[dict[str, Any]], set_id: str) -> None:
    """追加题目并同步所属题集的题量与真实更新时间（新建题集时自动补一条）。

    双写：内存 + MySQL（尽力而为，库不可用只记日志不影响主流程）。
    """
    state.questions.extend(items)
    target = next((s for s in state.sets if s["id"] == set_id), None)
    if target is None:
        return
    target["questionCount"] = sum(1 for q in state.questions if q["setId"] == set_id)
    target["updatedAt"] = now_str()
    db.save_set(target)
    db.save_questions(items)


def record_answer_event(user_id: str, set_id: str, question: dict[str, Any],
                        choice: str, correct: bool) -> None:
    """真实作答事件聚合：每次提交记一次，样本量达标时回写全站答对率。

    内存计数即时生效（题目 siteCorrectRate 当场更新，GET /api/questions 可见）；
    事件明细与回写尽力而为落库，重启后由 RuntimeState.__post_init__ 重新聚合恢复。
    低样本保护：attempts < MIN_SAMPLE 时不覆盖展示值（种子题保留演示数据 / 生成题保持 None）。
    """
    stats = state.answer_stats.setdefault(question["id"], {"attempts": 0, "correct": 0})
    stats["attempts"] += 1
    stats["correct"] += 1 if correct else 0
    if stats["attempts"] >= MIN_SAMPLE:
        question["siteCorrectRate"] = round(stats["correct"] * 100 / stats["attempts"])
        db.update_site_correct_rate(question["id"], question["siteCorrectRate"])
    db.save_answer_event(user_id, set_id, question["id"], choice, correct)
    # 周图表 / 周报的内存模式源（MySQL 模式直查 answer_events，这里只作留底）
    state.answer_log.append({
        "userId": user_id,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "correct": 1 if correct else 0,
    })
    if len(state.answer_log) > ANSWER_LOG_CAP:
        del state.answer_log[: len(state.answer_log) - ANSWER_LOG_CAP]


# ---------------- 出题任务访问 ----------------


def active_generate_task(user_id: str) -> GenerateTask | None:
    """当前用户最近的未完成任务：优先「运行中」，其次最近一次「中断」。

    前端进入生成页时先查这里：running → 恢复进度条订阅；interrupted → 提示继续补齐。
    """
    candidates = [
        t
        for t in state.generate_tasks.values()
        if t.user_id == user_id and (not t.done or t.interrupted)
    ]
    return max(candidates, key=lambda t: t.created_at, default=None)


def add_set(new_set: dict[str, Any]) -> None:
    """新建题集：内存置顶 + 写库（题集是题目的外键父表，必须先落）。"""
    state.sets.insert(0, new_set)
    db.save_set(new_set)


def remove_set(set_id: str) -> bool:
    """删除题集：连带清掉内存题目并同步删库，返回是否存在。"""
    before = len(state.sets)
    state.sets = [s for s in state.sets if s["id"] != set_id]
    if len(state.sets) == before:
        return False
    state.questions = [q for q in state.questions if q["setId"] != set_id]
    db.delete_set(set_id)
    return True


# ---------------- 用户维度状态访问（JWT 多用户；懒加载 + 种子兜底） ----------------


def ensure_user(user_id: str) -> None:
    """首次触达该用户时从 MySQL 加载其全部状态；库不可用/行不存在时用模板兜底。

    幂等：已加载过的用户直接返回，请求内重复调用无额外开销。
    """
    if user_id in state.profiles:
        return
    saved = db.load_user(user_id)
    if saved:
        state.profiles[user_id] = saved["profile"]
        state.settings_map[user_id] = saved["settings"]
        state.deactivations[user_id] = saved["deactivation"]
        state.progress[user_id] = saved["progress"]
        state.wrong_book[user_id] = saved["wrongBook"]
        state.favorites[user_id] = saved["favorites"]
        state.resumes[user_id] = {
            "analysis": saved["resumeAnalysis"],
            "text": saved["resumeText"],
            "summary": saved["resumeSummary"],
        }
        state.resume_versions[user_id] = db.load_resumes(user_id)
        state.notifications[user_id] = db.load_notifications(user_id)
        # 今日计划懒加载（跨日由 GET /api/plans 按 date 惰性补拉 / 生成）
        state.plans[user_id] = {
            datetime.now().strftime("%Y-%m-%d"): db.load_plans(
                user_id, datetime.now().strftime("%Y-%m-%d")
            )
        }
    else:
        state.profiles[user_id] = {}
        state.settings_map[user_id] = dict(USER_SETTINGS_SEED)
        state.deactivations[user_id] = None
        state.progress[user_id] = {}
        state.wrong_book[user_id] = []
        state.favorites[user_id] = []
        state.resumes[user_id] = None
        state.resume_versions[user_id] = []
        state.notifications[user_id] = []
        state.plans[user_id] = {}


def user_profile(user_id: str) -> dict[str, Any]:
    """完整资料：演示账号叠加种子（林晓）；其余用户零值起步，仅由真实数据/打卡推进。"""
    base = USER if user_id == DEMO_USER_ID else _NEW_USER_BASE
    return {**base, **state.profiles.get(user_id, {})}


def user_settings(user_id: str) -> dict[str, Any]:
    return {**USER_SETTINGS_SEED, **state.settings_map.get(user_id, {})}


def user_deactivation(user_id: str) -> dict[str, Any] | None:
    return state.deactivations.get(user_id)


def user_resume(user_id: str) -> dict[str, Any] | None:
    return state.resumes.get(user_id)


# ---------------- 简历多版本（批 3：resumes v2 表 + 内存列表双写） ----------------


def user_resume_versions(user_id: str) -> list[dict[str, Any]]:
    """该用户全部简历版本（version 降序；analysis 内可能含体检 checkup）。"""
    return state.resume_versions.get(user_id, [])


def get_resume_version(user_id: str, resume_id: str) -> dict[str, Any] | None:
    for rec in state.resume_versions.get(user_id, []):
        if rec["resumeId"] == resume_id:
            return rec
    return None


def add_resume_version(user_id: str, *, file_name: str, text: str, summary: str,
                       analysis: dict[str, Any] | None,
                       is_optimized: bool = False) -> dict[str, Any]:
    """新增一个简历版本：内存列表头部插入 + MySQL 尽力而为写一行。

    同时把最新一份同步进 state.resumes，保持出题链路（generation / plans /
    question_sets 读 user_resume）兼容。
    """
    versions = state.resume_versions.setdefault(user_id, [])
    next_version = max((int(r.get("version", 0)) for r in versions), default=0) + 1
    rec = {
        "resumeId": f"res-{uuid4().hex[:12]}",
        "userId": user_id,
        "version": next_version,
        "isOptimized": is_optimized,
        "fileName": file_name,
        "text": text,
        "summary": summary,
        "analysis": analysis,
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    versions.insert(0, rec)
    state.resumes[user_id] = {"analysis": analysis, "text": text, "summary": summary}
    db.save_resume_record(rec)
    return rec


def update_resume_version(user_id: str, resume_id: str,
                          analysis: dict[str, Any] | None) -> None:
    """更新版本的结构化结果（体检惰性补算写回；若是最新版同步 state.resumes）。"""
    rec = get_resume_version(user_id, resume_id)
    if not rec:
        return
    rec["analysis"] = analysis
    versions = state.resume_versions.get(user_id) or []
    latest = state.resumes.get(user_id)
    if versions and versions[0]["resumeId"] == resume_id and latest is not None:
        state.resumes[user_id] = {**latest, "analysis": analysis}
    db.save_resume_record(rec)


def drop_resume_version(user_id: str, resume_id: str) -> bool:
    """删除一个简历版本；删的是最新版时把 state.resumes 回退到次新版本。"""
    versions = state.resume_versions.setdefault(user_id, [])
    index = next((i for i, r in enumerate(versions) if r["resumeId"] == resume_id), -1)
    if index < 0:
        return False
    versions.pop(index)
    if index == 0:
        if versions:
            head = versions[0]
            state.resumes[user_id] = {
                "analysis": head.get("analysis"),
                "text": head.get("text", ""),
                "summary": head.get("summary", ""),
            }
        else:
            state.resumes[user_id] = None
    db.delete_resume(user_id, resume_id)
    return True


# ---------------- 站内通知（批 4：内存列表 + notifications 表尽力而为双写） ----------------

NOTIFICATION_TYPES = ("generate_done", "exam_report", "review_due")
# 内存态上限：站内信只增不减，超过后裁剪最旧（库内不限，展示层 LIMIT 100）
NOTIFICATIONS_MAX = 200


def user_notifications(user_id: str) -> list[dict[str, Any]]:
    """该用户站内信（created_at 降序，最新在前）。"""
    return state.notifications.get(user_id, [])


def add_notification(user_id: str, ntype: str, payload: dict[str, Any] | None = None) -> dict:
    """写一条站内信（内存头部插入 + MySQL 尽力而为写一行）。"""
    rec = {
        "id": f"ntf-{uuid4().hex[:12]}",
        "userId": user_id,
        "type": ntype,
        "payload": payload,
        "read": False,
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    items = state.notifications.setdefault(user_id, [])
    items.insert(0, rec)
    del items[NOTIFICATIONS_MAX:]
    db.save_notification(rec)
    return rec


def mark_notification_read(user_id: str, notification_id: str) -> bool:
    """单条已读；db 行数与内存行任一命中即成功。"""
    db_hit = db.mark_notification_read(user_id, notification_id)
    mem_hit = False
    for rec in state.notifications.get(user_id, []):
        if rec["id"] == notification_id:
            rec["read"] = True
            mem_hit = True
            break
    return db_hit or mem_hit


def mark_all_notifications_read(user_id: str) -> int:
    """全部已读；返回内存更新的条数（db 同步尽力而为）。"""
    db.mark_all_notifications_read(user_id)
    count = 0
    for rec in state.notifications.get(user_id, []):
        if not rec["read"]:
            rec["read"] = True
            count += 1
    return count


def find_notification_by_date(user_id: str, ntype: str, day: str) -> dict[str, Any] | None:
    """查某类型通知当日是否已写过（复习到期汇总去重用，day 形如 2026-01-01）。"""
    for rec in state.notifications.get(user_id, []):
        if rec["type"] == ntype and str(rec["createdAt"]).startswith(day):
            return rec
    return None


# ---------------- 账号内存兜底（MySQL 不可用时的注册/登录） ----------------


def save_account(user_id: str, account: str, password_hash: str, profile: dict[str, Any]) -> None:
    """注册信息写入内存：账号可登录、资料以注册项为底（不叠加种子，避免新用户串资料）。

    MySQL 可用后 db.create_user 仍是主路径，此函数只是让内存模式闭环。
    """
    state.accounts[account] = {"userId": user_id, "passwordHash": password_hash}
    state.profiles[user_id] = dict(profile)


def find_account(account: str) -> dict[str, str] | None:
    """登录查号的内存兜底：db.get_user_by_account 未命中时调用。"""
    return state.accounts.get(account)


# ---------------- 用户数据持久化钩子（路由改内存后调用，尽力而为写 MySQL） ----------------


def persist_user(user_id: str) -> None:
    """资料 / 偏好 / 注销申请变更后整行 upsert（users 表一行全量）。"""
    db.save_user(user_id, user_profile(user_id), user_settings(user_id),
                 state.deactivations.get(user_id))


def persist_answer(user_id: str, set_id: str, question_id: str, choice: str) -> None:
    db.save_progress(user_id, set_id, question_id, choice)


def persist_progress_reset(user_id: str, set_id: str) -> None:
    db.delete_progress(user_id, set_id)


def persist_favorite(user_id: str, question_id: str, on: bool) -> None:
    if on:
        db.save_favorite(user_id, question_id)
    else:
        db.delete_favorite(user_id, question_id)


def persist_wrong_item(user_id: str, item: dict[str, Any]) -> None:
    db.save_wrong_item(user_id, item)


# ---------------- 模拟考试（二期） ----------------


EXAM_MAX_QUESTIONS = 40       # 单卷题量上限（客观题抽取）
EXAM_SEC_PER_QUESTION = 90    # 限时 = 题数 × 90 秒
EXAM_OBJECTIVE_TYPES = ("single_choice", "multi_choice", "judge")


def get_exam(user_id: str, exam_id: str) -> dict[str, Any] | None:
    """取本人的考试：内存优先，未命中从 MySQL 拉（跨重启恢复 running 场景）。"""
    exam = state.exams.get(exam_id)
    if exam is None:
        exam = db.load_exam(user_id, exam_id)
        if exam is not None:
            state.exams[exam_id] = exam
    if exam is None or exam.get("userId") != user_id:
        return None
    return exam


def persist_exam(exam: dict[str, Any]) -> None:
    """考试状态变更后落库（答题/暂停/恢复/交卷都会调，覆盖写全行）。"""
    db.save_exam_record(exam)


def user_exams(user_id: str) -> list[dict[str, Any]]:
    """历史列表：MySQL 可用直查，内存模式从 state.exams 过滤倒序。"""
    records = db.load_exams(user_id)
    if records:
        return records
    mine = [e for e in state.exams.values() if e.get("userId") == user_id]
    mine.sort(key=lambda e: e.get("createdAt", ""), reverse=True)
    return mine


def done_scores_by_role(bucket_role: str) -> list[int]:
    """同岗位分桶已交卷分数：MySQL 直查优先，内存模式回退聚合。"""
    scores = db.load_done_scores(bucket_role)
    if scores:
        return scores
    return sorted(
        e["score"] for e in state.exams.values()
        if e.get("bucketRole") == bucket_role and e.get("status") == "done"
        and e.get("score") is not None
    )


# ---------------- 学习计划持久化（二期） ----------------


def today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def today_plans(user_id: str) -> list[dict[str, Any]]:
    return state.plans.setdefault(user_id, {}).setdefault(today_key(), [])


def persist_plan(user_id: str, item: dict[str, Any]) -> None:
    db.save_plan(user_id, today_key(), item)


def drop_plan(user_id: str, plan_id: str) -> None:
    db.delete_plan(user_id, plan_id)


def user_week_events(user_id: str, days: int = 7) -> list[dict[str, Any]]:
    """近 N 天逐日作答聚合：MySQL 可用直查 answer_events，否则用内存 answer_log。"""
    events = db.load_week_events(user_id, days)
    if events:
        return events
    # 内存模式：按日聚合 answer_log
    today = datetime.now().date()
    buckets: dict[str, dict[str, int]] = {}
    for offset in range(days):
        day = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        buckets[day] = {"date": day, "answered": 0, "correct": 0}
    for ev in state.answer_log:
        if ev["userId"] != user_id or ev["date"] not in buckets:
            continue
        buckets[ev["date"]]["answered"] += 1
        buckets[ev["date"]]["correct"] += int(ev["correct"])
    return sorted(buckets.values(), key=lambda b: b["date"])


def user_answer_totals(user_id: str) -> dict[str, int]:
    """累计作答口径（全部时间）：MySQL 直查 answer_events，否则聚合内存 answer_log。"""
    totals = db.load_user_answer_totals(user_id)
    if totals is not None:
        return totals
    answered = 0
    correct = 0
    for ev in state.answer_log:
        if ev["userId"] != user_id:
            continue
        answered += 1
        correct += int(ev["correct"])
    return {"answered": answered, "correct": correct}


def persist_set_deletion(user_id: str, set_id: str) -> None:
    """删除题集时同步清理该用户该题集的作答进度与错题（与内存行为一致）。"""
    db.delete_progress(user_id, set_id)
    db.delete_wrong_items(user_id, set_id)


def purge_user_data(user_id: str) -> None:
    """注销执行：物理清理该用户全部数据行，并清空全站题库（与内存清理对齐）。"""
    db.clear_user_data(user_id)
    db.clear_bank()


# ---------------- 岗位检索缓存（三期，内存镜像 + MySQL 双写，TTL 见下） ----------------

# 检索列表 TTL 收紧到 1 天：岗位「是否还在招 / 薪资是否变了」的新鲜度优先，
# 代价是同一关键词每天首次检索会真实打一次平台（节流与风控口径不变）。
JOBS_CACHE_TTL_DAYS = 1
# JD 详情与考点地图保持 7 天：详情为单卡惰性抓取（风控最敏感、JD 文本变动小），
# 考点地图是本侧生成的报告（过期即消失、需重新生成题库才有，缩短会造成体验倒退）。
JOB_DETAIL_TTL_DAYS = 7
JOB_MAP_TTL_DAYS = 7


def jobs_cache_key(keyword: str, city: str) -> str:
    """缓存 key：sha256(关键词|城市) 截断 32 位（与 schema 注释口径一致）。"""
    return sha256(f"{keyword.strip()}|{city.strip()}".encode("utf-8")).hexdigest()[:32]


def _cache_expiry(days: int) -> tuple[datetime, str]:
    """过期时刻：内存存 datetime，库存 'YYYY-MM-DD HH:MM:SS' 字符串。"""
    expires = datetime.now() + timedelta(days=days)
    return expires, expires.strftime("%Y-%m-%d %H:%M:%S")


def _reload_expiry(days: int) -> datetime:
    """db 回源条目的内存过期时刻：库行不回传 expires_at，保守取剩余 TTL 的一半（下限 1 小时）。

    下限必不可少：TTL=1 天时 `days // 2` 会算成 0，内存条目写进去就立即过期，
    导致每次读取都回源打库（内存镜像等于失效）。
    """
    return datetime.now() + max(timedelta(days=days) / 2, timedelta(hours=1))


def jobs_cache_get(cache_key: str) -> dict[str, Any] | None:
    """未过期缓存（内存 -> db 回源并回填内存）；过期条目即剔（惰性过期刷新）。"""
    entry = state.jobs_cache.get(cache_key)
    if entry:
        if entry["expiresAt"] > datetime.now():
            return entry
        state.jobs_cache.pop(cache_key, None)
    row = db.load_jobs_cache(cache_key, JOBS_CACHE_TTL_DAYS)
    if not row:
        return None
    # 库行无过期时刻字段回传，统一按剩余 TTL 的中点给内存条目（避免反复回源）
    loaded = {**row, "expiresAt": _reload_expiry(JOBS_CACHE_TTL_DAYS)}
    state.jobs_cache[cache_key] = loaded
    return loaded


def jobs_cache_put(keyword: str, city: str, payload: list[dict[str, Any]]) -> str:
    """写入/覆盖缓存（同 key 重查刷新 TTL），返回 cache_key。"""
    cache_key = jobs_cache_key(keyword, city)
    expires, expires_str = _cache_expiry(JOBS_CACHE_TTL_DAYS)
    entry = {
        "keyword": keyword.strip(),
        "city": city.strip(),
        "payload": payload,
        "expiresAt": expires,
    }
    state.jobs_cache[cache_key] = entry
    db.save_jobs_cache(cache_key, entry["keyword"], entry["city"], payload, expires_str)
    return cache_key


def job_detail_get(security_id: str) -> dict[str, Any] | None:
    """未过期 JD 详情（内存 -> db 回源并回填）。"""
    entry = state.job_details.get(security_id)
    if entry:
        if entry["expiresAt"] > datetime.now():
            return entry["payload"]
        state.job_details.pop(security_id, None)
    payload = db.load_job_detail(security_id)
    if payload is None:
        return None
    state.job_details[security_id] = {
        "payload": payload,
        "expiresAt": _reload_expiry(JOB_DETAIL_TTL_DAYS),
    }
    return payload


def job_detail_put(security_id: str, keyword: str, payload: dict[str, Any]) -> None:
    """JD 详情写入/覆盖。"""
    expires, expires_str = _cache_expiry(JOB_DETAIL_TTL_DAYS)
    state.job_details[security_id] = {"payload": payload, "expiresAt": expires}
    db.save_job_detail(security_id, keyword, payload, expires_str)


def job_map_get(map_key: str) -> dict[str, Any] | None:
    """未过期考点地图报告（内存 -> db 回源并回填）。"""
    entry = state.job_maps.get(map_key)
    if entry:
        if entry["expiresAt"] > datetime.now():
            return entry["payload"]
        state.job_maps.pop(map_key, None)
    payload = db.load_job_map(map_key)
    if payload is None:
        return None
    state.job_maps[map_key] = {
        "payload": payload,
        "expiresAt": _reload_expiry(JOB_MAP_TTL_DAYS),
    }
    return payload


def job_map_put(keyword: str, city: str, payload: dict[str, Any]) -> str:
    """考点地图报告写入/覆盖，返回 map_key（与检索缓存同 hash 口径）。"""
    map_key = jobs_cache_key(keyword, city)
    expires, expires_str = _cache_expiry(JOB_MAP_TTL_DAYS)
    state.job_maps[map_key] = {"payload": payload, "expiresAt": expires}
    db.save_job_map(map_key, keyword.strip(), city.strip(), payload, expires_str)
    return map_key


def daily_get(user_id: str, practice_date: str) -> dict[str, Any] | None:
    """当日每日一练（内存 -> db 回源并回填）；无行返回 None（由调用方惰性生成）。"""
    entry = state.daily_practices.get(user_id)
    if entry and entry["date"] == practice_date:
        return entry
    row = db.load_daily_practice(user_id, practice_date)
    if row is None:
        return None
    entry = {
        "id": row["id"],
        "date": practice_date,
        "questionIds": row["questionIds"],
        "doneIds": row["doneIds"],
    }
    state.daily_practices[user_id] = entry
    return entry


def daily_put(user_id: str, entry: dict[str, Any]) -> None:
    """每日一练写入/覆盖（一人一天一行，题目快照当日不变）。"""
    state.daily_practices[user_id] = entry
    db.save_daily_practice(
        entry["id"], user_id, entry["date"], entry["questionIds"], entry["doneIds"]
    )


# ---------------- 求职看板（四期，内存镜像 + MySQL 双写） ----------------

PIPELINE_STAGES = ["applied", "written", "interview", "offer"]


def _pipeline_ensure(user_id: str) -> list[dict[str, Any]]:
    """取该用户看板卡列表（内存未命中时从 db 回源并回填）。

    读库瞬时失败（返回 None）先重试一次再降级：若首次失败就把空列表写进内存，
    该用户看板会被「清空」到进程重启为止（db 行仍在，但内存不再回源），
    并连带单岗位专属出题解析不到岗位名 / 挂接不上看板卡。
    """
    if user_id in state.job_pipeline:
        return state.job_pipeline[user_id]
    rows = db.load_pipeline(user_id)
    if rows is None:
        rows = db.load_pipeline(user_id)
    state.job_pipeline[user_id] = rows if rows is not None else []
    return state.job_pipeline[user_id]


def pipeline_cards(user_id: str, include_trash: bool = False) -> list[dict[str, Any]]:
    """看板卡列表（默认排除回收站）。"""
    cards = _pipeline_ensure(user_id)
    return [c for c in cards if include_trash or not c.get("inTrash")]


def pipeline_find(user_id: str, card_id: str) -> dict[str, Any] | None:
    """按 id 查本人看板卡（含回收站）；不存在/非本人返回 None。"""
    return next((c for c in _pipeline_ensure(user_id) if c["id"] == card_id), None)


def pipeline_add(user_id: str, card: dict[str, Any]) -> dict[str, Any]:
    """新增看板卡（写内存 + 尽力而为写库）。"""
    cards = _pipeline_ensure(user_id)
    card.setdefault("inTrash", False)
    cards.append(card)
    db.save_pipeline_card(card)
    return card


def pipeline_save(user_id: str, card: dict[str, Any]) -> None:
    """看板卡变更后回写（内存已原地改，此处只同步 db）。"""
    db.save_pipeline_card(card)


def pipeline_purge(user_id: str, card_id: str) -> bool:
    """彻底删除看板卡（从内存移除 + 删库）；命中返回 True。"""
    cards = _pipeline_ensure(user_id)
    target = next((c for c in cards if c["id"] == card_id), None)
    if target is None:
        return False
    cards.remove(target)
    db.delete_pipeline_card(card_id)
    return True


def set_practice_progress(user_id: str, set_id: str) -> dict[str, int]:
    """题集练习完成度（与题库中心口径一致）：done=已作答题数，total=题集总题数。"""
    if not set_id:
        return {"done": 0, "total": 0}
    total = sum(1 for q in state.questions if q["setId"] == set_id)
    done = len(state.progress.get(user_id, {}).get(set_id, {}))
    return {"done": min(done, total), "total": total}


# ---------------- 语音模拟面试（五期，内存镜像 + MySQL 双写） ----------------


def interview_session(user_id: str, session_id: str) -> dict[str, Any] | None:
    """取本人的面试会话：内存优先，未命中从 MySQL 拉（跨重启恢复 running 场景）。"""
    session = state.interview_sessions.get(session_id)
    if session is None:
        session = db.load_interview_session(user_id, session_id)
        if session is not None:
            state.interview_sessions[session_id] = session
    if session is None or session.get("userId") != user_id:
        return None
    return session


def persist_interview_session(session: dict[str, Any]) -> None:
    """面试会话变更后落库（创建/答题/结束都调，覆盖写全行）+ 同步内存镜像。"""
    state.interview_sessions[session["id"]] = session
    db.save_interview_session(session)


def user_interview_sessions(user_id: str) -> list[dict[str, Any]]:
    """历史列表：MySQL 可用直查，内存模式从 state 过滤倒序。"""
    records = db.load_interview_sessions(user_id)
    if records:
        return records
    mine = [s for s in state.interview_sessions.values() if s.get("userId") == user_id]
    mine.sort(key=lambda s: s.get("startedAt", ""), reverse=True)
    return mine


def interview_answers(session_id: str) -> list[dict[str, Any]]:
    """某会话全部作答（按题序升序）：MySQL 直查优先，内存模式回退会话内嵌列表。"""
    rows = db.load_interview_answers(session_id)
    if rows:
        return rows
    session = state.interview_sessions.get(session_id)
    return list((session or {}).get("answers", []))


def persist_interview_answer(answer: dict[str, Any]) -> None:
    """面试作答落库 + 同步内存会话内嵌列表（重新作答按 seq 覆盖）。"""
    session = state.interview_sessions.get(answer.get("sessionId", ""))
    if session is not None:
        answers = session.setdefault("answers", [])
        seq = answer.get("seq")
        index = next((i for i, a in enumerate(answers) if a.get("seq") == seq), None)
        if index is None:
            answers.append(answer)
        else:
            answers[index] = answer
        answers.sort(key=lambda a: a.get("seq", 0))
    db.save_interview_answer(answer)


def interview_turns(answer_id: str) -> list[dict[str, Any]]:
    """某作答的全部追问轮次（按轮次升序）：MySQL 直查。"""
    return db.load_interview_turns(answer_id)


def persist_interview_turn(turn: dict[str, Any]) -> None:
    """追问轮次落库（append-only）。"""
    db.save_interview_turn(turn)


def drop_interview_session(session_id: str) -> None:
    """彻底删除面试会话（内存 + 库级联清作答/追问；放弃/注销清理用）。"""
    state.interview_sessions.pop(session_id, None)
    db.delete_interview_session(session_id)
