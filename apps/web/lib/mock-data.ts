import type {
  PlanTask,
  Question,
  QuestionSet,
  ResumeAnalysis,
  UserProfile,
  UserSettings,
  WrongItem,
} from "./types";

/** ================= 用户（原型角色：林晓） ================= */

export const MOCK_USER: UserProfile = {
  name: "林晓",
  avatarText: "林",
  targetRole: "产品经理",
  years: 3,
  streak: 12,
  totalAnswered: 1024,
  correctRate: 78,
  phone: "138****6021",
  wechatBound: true,
};

/** P14 我的页偏好（一期仅复习提醒） */
export const MOCK_USER_SETTINGS: UserSettings = {
  reviewReminderEnabled: true,
  reviewReminderTime: "20:00",
};

/** 注销冷静期（天）：期内可撤回，逾期不可恢复 */
export const DEACTIVATION_COOLING_DAYS = 7;

/** 注销时删除的数据范围（二次确认弹窗向用户明示） */
export const DELETION_SCOPES = [
  "个人资料与账号绑定（手机号 / 微信）",
  "题集与刷题进度、答题记录",
  "错题本与复习调度队列",
  "收藏与笔记",
  "简历原文件与解析产物",
  "学习统计与周报数据",
];

/** ================= 今日学习计划（原型 P1 文案） ================= */

export const MOCK_PLANS: PlanTask[] = [
  { id: "plan-1", type: "practice", title: "产品经理核心题集 · 刷 20 题", estMinutes: 15, done: true },
  { id: "plan-2", type: "review", title: "错题复习 · 艾宾浩斯第 2 天", estMinutes: 10, done: true },
  { id: "plan-3", type: "interview", title: "AI 模拟面试 · 行为面专场 1 场", estMinutes: 25, done: true },
  { id: "plan-4", type: "jd_set", title: "JD 定制题集 · 字节跳动后端岗", estMinutes: 20, done: false },
  { id: "plan-5", type: "resume_check", title: "简历体检 · 查看 AI 优化建议", estMinutes: 5, done: false },
];

/** 本周学习柱状图（周一~周日，周六最高，合计 128） */
export const MOCK_WEEK_BARS = [
  { day: "一", value: 15 },
  { day: "二", value: 18 },
  { day: "三", value: 12 },
  { day: "四", value: 20 },
  { day: "五", value: 16 },
  { day: "六", value: 32 },
  { day: "日", value: 15 },
];

export const MOCK_WEEK_STATS = { answered: 128, correctRate: 76, pendingReview: 23 };

/** ================= 题集（原型 P2 文案） ================= */

export const MOCK_SETS: QuestionSet[] = [
  { id: "set-1", source: "resume", title: "产品经理核心题集", questionCount: 6, updatedAt: "更新于 2 小时前" },
  { id: "set-2", source: "jd_target", title: "字节跳动 · 后端开发", questionCount: 6, updatedAt: "更新于昨天" },
  { id: "set-3", source: "job_search", title: "数据分析师高频题", questionCount: 4, updatedAt: "更新于 3 天前" },
  { id: "set-4", source: "resume", title: "前端工程师全攻略", questionCount: 3, updatedAt: "更新于 3 天前" },
  { id: "set-5", source: "mock_interview", title: "行为面 STAR 专场", questionCount: 3, updatedAt: "更新于上周" },
  { id: "set-6", source: "jd_target", title: "腾讯 · 产品经理校招", questionCount: 3, updatedAt: "更新于上周" },
  { id: "set-7", source: "job_search", title: "Java 后端进阶", questionCount: 3, updatedAt: "更新于 2 周前" },
  { id: "set-8", source: "resume", title: "运营岗通用题集", questionCount: 2, updatedAt: "更新于 2 周前" },
  { id: "set-9", source: "jd_target", title: "阿里 · 数据产品经理", questionCount: 2, updatedAt: "更新于 3 周前" },
];

/** ================= 题目池 ================= */

const Q = (
  q: Omit<Question, "type" | "difficulty"> & Partial<Pick<Question, "type" | "difficulty">>,
): Question => ({
  type: "single_choice",
  difficulty: 2,
  ...q,
});

const BASE_QUESTIONS: Question[] = [
  // ---- set-1 产品经理核心题集 ----
  Q({
    id: "q-p1",
    setId: "set-1",
    category: "专业技术",
    stem: "在制定产品 roadmap 时，以下哪种优先级评估方法最适合资源有限的初创团队？",
    options: ["RICE 评分模型", "MoSCoW 法则", "KANO 模型分类", "SWOT 分析"],
    answer: "B",
    explanation:
      "初创团队资源有限，MoSCoW 通过 Must/Should/Could/Won't 四级划分，能在需求远超资源时快速砍掉非核心项；RICE 需要较准确的量化预估数据，初创期数据不足时误差大。",
    referenceAnswer:
      "先说明选择理由（资源约束 → 快速分层），再结合一个真实取舍案例，最后补充团队对齐机制（每周 roadmap 评审）。",
    knowledgeTags: ["roadmap", "优先级", "MoSCoW"],
    siteCorrectRate: 62,
  }),
  Q({
    id: "q-p3",
    setId: "set-1",
    category: "专业技术",
    stem: "一份高质量的 PRD 最应该优先保证下列哪一点？",
    options: [
      "交互细节覆盖所有极端分支",
      "需求背景、目标与衡量指标清晰",
      " UI 稿像素级标注完整",
      "技术实现方案详尽",
    ],
    answer: "B",
    explanation:
      "PRD 的第一要务是让团队理解「为什么做、做成什么样算成功」。背景、目标与衡量指标缺失时，再细的交互稿也会让研发在实现中反复返工。",
    referenceAnswer:
      "作答框架：先给结论（目标导向），再展开 PRD 结构（背景/目标/范围/规则/指标），最后举一例说明目标不清导致的返工。",
    knowledgeTags: ["PRD", "需求文档"],
    siteCorrectRate: 71,
  }),
  Q({
    id: "q-p4",
    setId: "set-1",
    category: "专业技术",
    stem: "想验证「新用户是否在 7 天内体会到产品核心价值」，以下哪种方法最直接？",
    options: ["问卷调研 NPS", "用户访谈", "新手期行为漏斗与留存分析", "竞品分析"],
    answer: "C",
    explanation:
      "「体会到核心价值」是行为问题而非态度问题，应看新用户关键行为（aha moment）的完成率与 7 日留存的关联；问卷和访谈适合做补充定性验证。",
    referenceAnswer:
      "先定义「核心价值行为」，再看新用户 7 天内完成该行为的比例及与留存的相关性，必要时用访谈解释行为背后的原因。",
    knowledgeTags: ["用户调研", "留存", "aha moment"],
    siteCorrectRate: 58,
  }),
  Q({
    id: "q-p5",
    setId: "set-1",
    category: "专业技术",
    stem: "关于北极星指标，以下哪项理解是正确的？",
    options: [
      "北极星指标就是 DAU，适用于所有产品",
      "北极星指标应能反映用户获得的核心价值，并可拆解到团队行动",
      "北极星指标越多越好，便于全面监控",
      "北极星指标一旦确定就不能调整",
    ],
    answer: "B",
    explanation:
      "北极星指标的本质是「用户价值 × 商业价值」的交汇点，需要可拆解、可行动。不同产品/阶段北极星不同，也会随战略演进调整。",
    referenceAnswer:
      "定义 → 举例（如内容产品用「有效消费时长」而非 DAU）→ 说明如何拆解为输入指标分配到团队。",
    knowledgeTags: ["北极星指标", "指标体系"],
    siteCorrectRate: 66,
  }),
  Q({
    id: "q-p6",
    setId: "set-1",
    category: "项目深挖",
    stem: "你负责的功能上线后核心指标不涨反跌，第一步应该做什么？",
    options: [
      "立刻回滚版本",
      "拆分漏斗定位下跌环节，区分口径问题与真实下跌",
      "加大投放拉新稀释跌幅",
      "等待一周再观察",
    ],
    answer: "B",
    explanation:
      "先定位再决策：检查数据口径是否变化、按漏斗/人群/渠道拆分定位下跌环节，确认是真实下跌后再决定回滚或修复。盲目回滚可能误伤，盲目等待会扩大损失。",
    referenceAnswer:
      "STAR 框架：情境（指标下跌）→ 任务（快速定位）→ 行动（口径校验 + 维度下钻 + 分群对比）→ 结果（定位原因并量化挽回）。",
    knowledgeTags: ["数据分析", "指标异动"],
    siteCorrectRate: 74,
  }),
  Q({
    id: "q-p7",
    setId: "set-1",
    category: "专业技术",
    stem: "MVP（最小可行产品）的核心目的是什么？",
    options: [
      "用最低成本做出功能最全的版本",
      "用最小成本验证核心价值假设是否成立",
      "快速上线抢占市场，后续再补质量",
      "给投资人演示的演示版本",
    ],
    answer: "B",
    explanation:
      "MVP 服务于「验证假设」而非「交付功能」：用最小代价测试最关键的不确定性（用户是否真有此痛点、方案是否被接受），验证失败则低成本转向。",
    referenceAnswer:
      "先给定义，再举一个 MVP 形态例子（如人工兜底替代自动化），最后说明验证指标与下一步决策规则。",
    knowledgeTags: ["MVP", "精益创业"],
    siteCorrectRate: 80,
  }),

  // ---- set-2 字节跳动 · 后端开发 ----
  Q({
    id: "q-t1",
    setId: "set-2",
    category: "专业技术",
    stem: "MySQL 索引失效的场景中，以下哪一项描述是不正确的？",
    options: [
      "对索引列使用函数会导致索引失效",
      "LIKE 以 % 开头会导致索引失效",
      "联合索引不满足最左前缀可能失效",
      "使用覆盖索引一定会导致索引失效",
    ],
    answer: "D",
    explanation:
      "覆盖索引是优化手段而非失效场景：查询列都被索引覆盖时无需回表，性能更好。A/B/C 均为常见失效场景。",
    referenceAnswer:
      "先指出 D 错误并解释覆盖索引原理，再系统列举失效场景（函数、隐式转换、前导通配、最左前缀、OR 连接非索引列等），最后提一句用 EXPLAIN 验证。",
    knowledgeTags: ["MySQL", "索引", "覆盖索引"],
    siteCorrectRate: 55,
  }),
  Q({
    id: "q-t2",
    setId: "set-2",
    category: "专业技术",
    stem: "缓存穿透指的是请求打到数据库的直接原因是？",
    options: [
      "热点 key 过期瞬间被并发访问",
      "查询了缓存和数据库中都不存在的数据",
      "缓存整体宕机",
      "缓存数据与数据库不一致",
    ],
    answer: "B",
    explanation:
      "穿透 = 查「不存在」的数据，缓存永远 miss 直达 DB，常用布隆过滤器或空值缓存拦截。A 是击穿（热点 key），C 是雪崩。",
    referenceAnswer:
      "先辨析穿透/击穿/雪崩三兄弟，再给穿透的两种解法（布隆过滤器、空值短 TTL 缓存）及各自适用场景。",
    knowledgeTags: ["Redis", "缓存穿透", "布隆过滤器"],
    siteCorrectRate: 69,
  }),
  Q({
    id: "q-t3",
    setId: "set-2",
    category: "专业技术",
    stem: "用 Redis 实现分布式锁时，下列哪组实践最安全？",
    options: [
      "SETNX 之后单独再 EXPIRE",
      "SET key value NX PX + 唯一 value + Lua 脚本释放",
      "GETSET 循环重试",
      "直接用 INCR 计数",
    ],
    answer: "B",
    explanation:
      "加锁与过期必须原子（SET NX PX 一条命令）；value 存唯一标识防止误删他人锁，释放时用 Lua 保证「比对 + 删除」原子性。A 两步非原子，宕机会死锁。",
    referenceAnswer:
      "按「原子加锁 → 唯一标识 → Lua 释放 → 看门狗续期（Redisson）」逐层递进，并说明各层解决的故障场景。",
    knowledgeTags: ["Redis", "分布式锁"],
    siteCorrectRate: 63,
  }),
  Q({
    id: "q-t4",
    setId: "set-2",
    category: "专业技术",
    stem: "MySQL InnoDB 默认的事务隔离级别是？",
    options: ["读未提交", "读已提交", "可重复读", "串行化"],
    answer: "C",
    difficulty: 1,
    explanation:
      "InnoDB 默认 REPEATABLE READ（可重复读），通过 MVCC + Next-Key Lock 在该级别下同时解决了幻读问题，这是与标准 SQL 定义的差异点。",
    referenceAnswer:
      "先答「可重复读」，再展开四个隔离级别对照表，最后点出 InnoDB 用 Next-Key Lock 防幻读这一高频追问。",
    knowledgeTags: ["MySQL", "事务", "MVCC"],
    siteCorrectRate: 77,
  }),
  Q({
    id: "q-t5",
    setId: "set-2",
    category: "系统设计",
    stem: "设计秒杀系统时，减库存操作最合适的做法是？",
    options: [
      "请求直接打到数据库行锁扣减",
      "Redis 预减库存 + MQ 异步落库",
      "前端按钮置灰即可",
      "定时任务每分钟批量扣减",
    ],
    answer: "B",
    explanation:
      "秒杀核心是「限流削峰 + 最终一致」：Redis 原子预减扛住瞬时并发，MQ 异步落库削峰，配合令牌限流与防重。直接打 DB 行锁在十万级 QPS 下会瞬间打满连接。",
    referenceAnswer:
      "按链路讲：接入层限流 → Redis 预减（Lua 原子）→ MQ 削峰 → DB 最终一致 → 超卖/少卖的兜底对账。",
    knowledgeTags: ["秒杀", "高并发", "MQ"],
    siteCorrectRate: 70,
  }),
  Q({
    id: "q-t6",
    setId: "set-2",
    category: "专业技术",
    stem: "消息队列中防止消息丢失，下列哪项不是必要措施？",
    options: [
      "生产者开启发送确认（ACK）机制",
      "Broker 端消息持久化",
      "消费者处理成功后再手动 ACK",
      "消费者开启自动 ACK 提升吞吐",
    ],
    answer: "D",
    explanation:
      "自动 ACK 在消息收到即确认，处理失败就丢消息，恰恰是防丢失要避免的。A/B/C 分别覆盖生产、存储、消费三段的可靠性。",
    referenceAnswer:
      "按「生产者 → Broker → 消费者」三段模型分别给出可靠性措施，再点明自动 ACK 的风险与手动 ACK + 重试/死信队列的组合。",
    knowledgeTags: ["MQ", "可靠性", "ACK"],
    siteCorrectRate: 68,
  }),

  // ---- set-3 数据分析师高频题 ----
  Q({
    id: "q-d1",
    setId: "set-3",
    category: "专业技术",
    stem: "A/B 测试中，样本量不足会导致哪类统计风险显著上升？",
    options: [
      "第一类错误（假阳性）",
      "第二类错误（假阴性），检验功效不足",
      "指标口径漂移",
      "分流不均",
    ],
    answer: "B",
    explanation:
      "样本量不足时统计功效（Power）下降，真实存在的差异也检不出来，即第二类错误（假阴性）风险上升；因此实验前要做样本量预估（MDE/功效分析）。",
    referenceAnswer:
      "先给结论，再解释 α/β/Power 的关系，最后给出实践做法：实验前按 MDE 反推样本量，不足则不轻易下「无差异」结论。",
    knowledgeTags: ["A/B 测试", "假设检验", "统计功效"],
    siteCorrectRate: 48,
  }),
  Q({
    id: "q-d2",
    setId: "set-3",
    category: "专业技术",
    stem: "「各渠道转化率都在上升，但整体转化率却下降」最可能是什么现象？",
    options: ["数据埋点丢失", "辛普森悖论（渠道结构占比变化）", "指标计算口径错误", "季节性波动"],
    answer: "B",
    explanation:
      "辛普森悖论：分组趋势与整体趋势相反，根因是各组样本占比变化。例如低转化渠道流量占比上升，即使各渠道自身转化都在涨，整体也会被拉低。",
    referenceAnswer:
      "定义辛普森悖论 → 构造数字例子现场演算 → 给出应对：分析时固定结构（分层加权）看趋势。",
    knowledgeTags: ["辛普森悖论", "转化率"],
    siteCorrectRate: 61,
  }),
  Q({
    id: "q-d3",
    setId: "set-3",
    category: "专业技术",
    stem: "次日留存率的正确计算口径是？",
    options: [
      "第 2 天活跃 ÷ 第 1 天活跃",
      "某日新增用户中第 2 天仍活跃的人数 ÷ 该日新增用户数",
      "第 2 天新增 ÷ 第 1 天新增",
      "次日启动次数 ÷ 当日启动次数",
    ],
    answer: "B",
    difficulty: 1,
    explanation:
      "留存必须按「同期群（cohort）」计算：某天新增的用户群体里，第 2 天回来活跃的比例。A 混入了非当日新增的老用户，口径错误。",
    referenceAnswer:
      "给出公式并强调 cohort 概念，再延伸到 7 日/30 日留存与留存曲线形态（衰减趋平）。",
    knowledgeTags: ["留存率", "cohort"],
    siteCorrectRate: 82,
  }),
  Q({
    id: "q-d4",
    setId: "set-3",
    category: "专业技术",
    stem: "要计算「每个部门薪资排名第 2 高的员工」，最合适的 SQL 写法是？",
    options: [
      "GROUP BY + MAX 两次",
      "ROW_NUMBER() OVER (PARTITION BY 部门 ORDER BY 薪资 DESC) 取 rn=2",
      "LIMIT 2 OFFSET 1",
      "ORDER BY + HAVING",
    ],
    answer: "B",
    explanation:
      "分组内取 Top N 是窗口函数的经典场景：ROW_NUMBER 按部门分区排序后过滤 rn=2。GROUP BY 无法保留行级信息，LIMIT 不区分分组。",
    referenceAnswer:
      "写出完整 SQL，说明 PARTITION BY 与 ORDER BY 语义，并对比 RANK/DENSE_RANK 在并列场景的差异（高频追问）。",
    knowledgeTags: ["SQL", "窗口函数"],
    siteCorrectRate: 73,
  }),

  // ---- set-4 前端工程师全攻略 ----
  Q({
    id: "q-f1",
    setId: "set-4",
    category: "专业技术",
    stem: "关于浏览器关键渲染路径，下列哪项会阻塞首次渲染？",
    options: [
      "带有 async 属性的外链脚本",
      "CSSOM 未构建完成",
      "懒加载的图片",
      "带有 defer 属性的脚本",
    ],
    answer: "B",
    explanation:
      "Render Tree 由 DOM + CSSOM 合并而成，CSS 是渲染阻塞资源，CSSOM 未完成前页面无法绘制。async/defer 脚本不阻塞解析，懒加载图片同理。",
    referenceAnswer:
      "画出 DOM/CSSOM/Render Tree/Layout/Paint 流程 → 指出阻塞点 → 给优化手段（关键 CSS 内联、媒体查询拆分、defer 脚本）。",
    knowledgeTags: ["浏览器原理", "性能优化"],
    siteCorrectRate: 59,
  }),
  Q({
    id: "q-f2",
    setId: "set-4",
    category: "专业技术",
    stem: "关于 JavaScript 闭包，下列说法正确的是？",
    options: [
      "闭包会导致变量提升失效",
      "闭包是函数与其词法作用域的引用组合，可延长局部变量生命周期",
      "闭包只能在全局作用域创建",
      "闭包中的变量会被垃圾回收立即释放",
    ],
    answer: "B",
    difficulty: 1,
    explanation:
      "闭包 = 函数 + 其创建时的词法作用域。内层函数持有外层变量引用，使其不被回收，因此可用于私有化、柯里化，但滥用会造成内存驻留。",
    referenceAnswer:
      "定义 → 计数器示例 → 常见用途（防抖节流、私有变量）→ 内存注意事项（不再使用时解除引用）。",
    knowledgeTags: ["JavaScript", "闭包"],
    siteCorrectRate: 76,
  }),
  Q({
    id: "q-f3",
    setId: "set-4",
    category: "专业技术",
    stem: "React 中 setState 被设计为异步批量更新，主要目的是？",
    options: [
      "避免竞态条件",
      "合并多次更新减少渲染次数，提升性能",
      "为了兼容服务端渲染",
      "语法设计的历史遗留",
    ],
    answer: "B",
    explanation:
      "同一事件循环中的多次 setState 会被合并成一次渲染，避免连续 DOM 操作带来的性能损耗；这也是「setState 后立即读取 state 拿到旧值」的原因。",
    referenceAnswer:
      "先答性能合并，再演示批量更新示例，最后给出需要拿到最新值的正确姿势（函数式更新 / useEffect 依赖）。",
    knowledgeTags: ["React", "setState"],
    siteCorrectRate: 64,
  }),

  // ---- set-5 行为面 STAR 专场 ----
  Q({
    id: "q-b1",
    setId: "set-5",
    category: "行为面试",
    stem: "面试官问「讲一次你和研发发生严重分歧的经历」，以下哪种回答结构最佳？",
    options: [
      "强调自己最终说服了对方",
      "STAR 结构，重点放在行动中的沟通策略与最终业务结果",
      "回避冲突，说团队合作一直很顺利",
      "详细描述对方的问题所在",
    ],
    answer: "B",
    explanation:
      "行为题考察的是冲突中的协作能力而非输赢。STAR 中 Action 要突出换位沟通、数据佐证、共同目标对齐，Result 落到业务结果与关系沉淀。",
    referenceAnswer:
      "S：背景与分歧点 → T：你的职责 → A：先理解对方约束 → 用数据/原型对齐 → 折中方案 → R：按期上线 + 指标结果 + 后续协作机制。",
    knowledgeTags: ["行为面试", "STAR", "冲突处理"],
    siteCorrectRate: 84,
  }),
  Q({
    id: "q-b2",
    setId: "set-5",
    category: "行为面试",
    stem: "被问「讲一次失败经历」时，下列哪种做法最减分？",
    options: [
      "选择真实且与你直接相关的失败",
      "重点讲复盘方法与后续改进验证",
      "把失败归因于客观环境与他人",
      "量化说明改进后的结果",
    ],
    answer: "C",
    explanation:
      "失败题考察自我认知与成长型思维。甩锅式回答直接暴露低自省；高分回答是「真实失败 + 深度归因 + 可验证的改进」。",
    referenceAnswer:
      "选中等规模真实失败 → 归因到自身决策盲区 → 复盘方法论（如建立 checklist/数据验证机制）→ 后续项目验证改进有效。",
    knowledgeTags: ["行为面试", "失败经历"],
    siteCorrectRate: 79,
  }),
  Q({
    id: "q-b3",
    setId: "set-5",
    category: "行为面试",
    stem: "面试官追问「你刚才说的项目成果里，你个人的贡献占比是多少？」，最佳应对是？",
    options: [
      "谦虚表示都是团队的功劳",
      "给出具体可验证的个人动作与决策点",
      "强调自己是项目核心，其他人配合",
      "转移话题到项目整体成绩",
    ],
    answer: "B",
    explanation:
      "追问意图是辨别「搭车者」。应具体拆解自己独立完成的决策与动作（如方案选型、关键谈判、数据分析），既不过谦也不贪功，并给出可背调的验证点。",
    referenceAnswer:
      "拆解项目分工 → 明确个人独立负责的 2~3 个关键决策 → 说明这些决策对结果的因果贡献 → 提供可验证方式（文档/数据/引荐人）。",
    knowledgeTags: ["行为面试", "追问应对"],
    siteCorrectRate: 72,
  }),

  // ---- set-6 腾讯 · 产品经理校招 ----
  Q({
    id: "q-p2",
    setId: "set-6",
    category: "专业技术",
    stem: "以下哪一项不属于 KANO 模型中的需求分类？",
    options: ["基本型需求", "期望型需求", "兴奋型需求", "流量型需求"],
    answer: "D",
    explanation:
      "KANO 模型五类：基本型、期望型、兴奋型、无差异型、反向型。「流量型需求」是运营概念，不属于 KANO 分类。",
    referenceAnswer:
      "列出 KANO 五分类并各举一例（如 IM 产品：基本型=消息必达，期望型=传输速度，兴奋型=好玩的彩蛋），再说明如何通过 KANO 问卷判定分类。",
    knowledgeTags: ["KANO", "需求分析"],
    siteCorrectRate: 57,
  }),
  Q({
    id: "q-p8",
    setId: "set-6",
    category: "专业技术",
    stem: "校招产品面试常问「估算一个城市的网约车日订单量」，这类估算题的核心考察点是？",
    options: [
      "记住准确的行业数据",
      "结构化拆解问题的能力与清晰的假设链条",
      "心算速度",
      "对该公司业务的了解",
    ],
    answer: "B",
    explanation:
      "费米估算题考察拆解框架：人口 → 有出行需求人群 → 出行频次 → 网约车渗透率 → 订单量。数字本身不重要，假设合理、链条完整、会校验才是关键。",
    referenceAnswer:
      "先搭拆解树 → 逐层给出假设与依据 → 算出量级 → 用常识交叉校验（如与公开订单量级比对）。",
    knowledgeTags: ["费米估算", "结构化思维"],
    siteCorrectRate: 67,
  }),
  Q({
    id: "q-p9",
    setId: "set-6",
    category: "公司认知",
    stem: "被问「你为什么想来做 C 端产品而不是 B 端？」，哪种回答更有说服力？",
    options: [
      "C 端用户量大，更有成就感",
      "结合自身经历说明对 C 端用户洞察的热情与方法论匹配",
      "B 端太复杂，不想做",
      "听从导师建议",
    ],
    answer: "B",
    explanation:
      "动机题考察自我认知与岗位匹配。要把选择锚定到「个人经历/能力模型与岗位特性的匹配」，而非泛泛的比较或被动选择。",
    referenceAnswer:
      "个人经历（如校园产品/社团运营中的用户洞察案例）→ 提炼出的 C 端方法论（同理心、数据敏感）→ 与该岗位业务方向的结合点。",
    knowledgeTags: ["动机题", "岗位匹配"],
    siteCorrectRate: 75,
  }),
];

/** 同一内容在多个来源题集间复用（仅 Mock 阶段：克隆新 id 归属目标题集） */
function cloneTo(q: Question, setId: string, suffix: string): Question {
  return { ...q, id: `${q.id}${suffix}`, setId };
}

const byId = (id: string) => BASE_QUESTIONS.find((q) => q.id === id)!;

const CLONED: Question[] = [
  // set-7 Java 后端进阶
  cloneTo(byId("q-t2"), "set-7", "-s7"),
  cloneTo(byId("q-t3"), "set-7", "-s7"),
  cloneTo(byId("q-t5"), "set-7", "-s7"),
  // set-8 运营岗通用题集
  cloneTo(byId("q-p4"), "set-8", "-s8"),
  cloneTo(byId("q-p5"), "set-8", "-s8"),
  // set-9 阿里 · 数据产品经理
  cloneTo(byId("q-d1"), "set-9", "-s9"),
  cloneTo(byId("q-d2"), "set-9", "-s9"),
];

export const MOCK_QUESTIONS: Question[] = [...BASE_QUESTIONS, ...CLONED];

/** ================= 错题本（初始为空，由刷题答错自动累积） ================= */

export const MOCK_WRONG_BOOK: WrongItem[] = [];

/** 艾宾浩斯五档标签（阶段索引 0~4 对应 1/2/4/7/15 天） */
export const REVIEW_STAGE_LABELS = ["今天", "第 2 天", "第 4 天", "第 7 天", "第 15 天"];

/** 历史累计已掌握数（与当次会话无关的常驻底数） */
export const MOCK_MASTERED_BASE = 156;

/** ================= 简历解析结果（原型 P3 文案） ================= */

export const MOCK_RESUME_ANALYSIS: ResumeAnalysis = {
  fileName: "林晓-产品经理-简历.pdf",
  years: 3,
  targetRole: "产品经理",
  estimatedCount: 120,
  dimensions: [
    { label: "项目管理", score: 85 },
    { label: "数据分析", score: 72 },
    { label: "沟通协作", score: 90 },
    { label: "行业认知", score: 68 },
  ],
};

/** 出题流式进度的维度推进顺序（对齐覆盖矩阵） */
export const GENERATE_DIMENSIONS = [
  "技能八股",
  "项目深挖",
  "场景设计",
  "行为面试",
  "HR 综合",
  "压力/陷阱题",
];
