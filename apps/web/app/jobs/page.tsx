"use client";

/**
 * P4 岗位检索出题（三期引擎 B）。
 *
 * 链路：boss-agent-cli 状态引导（未安装 / 未登录扫码，只读检索声明）→ 关键词 + 城市
 * 检索（后端分层采样 + 缓存 1 天）→ 生成设置 chips（题量/难度/附答案）→ 触发市场题库
 * 生成（复用出题任务 SSE 进度）→ 考点地图（TOP10 技能榜 + 三档考点分布）。
 */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  addPipelineCard,
  getJobDetail,
  getJobMap,
  getJobsStatus,
  isLoggedIn,
  searchJobs,
  startBossBrowser,
  startJobGenerate,
  streamGenerate,
} from "@/lib/api";
import type { GenerateProgress, JobCard, JobMap, JobSearchResult, JobsStatus } from "@/lib/types";

const COUNT_OPTIONS = [20, 50, 80];
const DIFFICULTY_OPTIONS = ["混合", "L1", "L2", "L3"];
const TIER_META: { key: "core" | "bonus" | "diff"; label: string; cls: string }[] = [
  { key: "core", label: "必备高频", cls: "bg-brand/10 text-brand" },
  { key: "bonus", label: "加分项", cls: "bg-success/10 text-success" },
  { key: "diff", label: "差异化", cls: "bg-warn/10 text-warn" },
];

export default function JobsPage() {
  const [status, setStatus] = useState<JobsStatus | null>(null);
  const [keyword, setKeyword] = useState("");
  const [city, setCity] = useState("");
  const [result, setResult] = useState<JobSearchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // 专用浏览器（CDP 9222）：登录态 OK 但浏览器未连接时展示启动引导卡
  const [browserBusy, setBrowserBusy] = useState(false);
  const [browserMsg, setBrowserMsg] = useState("");

  // 生成设置（原型 P4 chips：题量默认 50 / 难度混合 / 附参考答案+解析默认开）
  const [count, setCount] = useState(50);
  const [difficulty, setDifficulty] = useState("混合");
  const [withAnswer, setWithAnswer] = useState(true);

  // 出题任务进度（复用引擎 A 的任务骨架与 SSE 协议）
  const [progress, setProgress] = useState<GenerateProgress | null>(null);
  const [genError, setGenError] = useState("");
  const [finishedSetId, setFinishedSetId] = useState("");

  // 考点地图（生成完成后可查）
  const [jobMap, setJobMap] = useState<JobMap | null>(null);

  // 岗位要求（四期增补）：单卡惰性拉全量 JD（缓存 7 天），按 securityId 缓存避免重复请求
  const [jdOpen, setJdOpen] = useState<Set<string>>(new Set());
  const [jdCache, setJdCache] = useState<Record<string, string>>({});
  const [jdLoading, setJdLoading] = useState<Set<string>>(new Set());

  // 加入求职看板（四期）：记录已加入的岗位，避免重复加卡 + toast 提示
  const [addedIds, setAddedIds] = useState<Set<string>>(new Set());
  const [addingId, setAddingId] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const notify = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 2400);
  };

  const refreshStatus = useCallback(() => {
    if (!isLoggedIn()) return;
    getJobsStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  // 搜索成功后顺手查一次考点地图（此前生成过则直接展示）
  useEffect(() => {
    if (!result) return;
    getJobMap(result.keyword, result.city)
      .then(setJobMap)
      .catch(() => setJobMap(null));
  }, [result]);

  const handleSearch = async () => {
    const kw = keyword.trim();
    if (!kw) return;
    setLoading(true);
    setError("");
    try {
      const res = await searchJobs(kw, city.trim());
      setResult(res);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "检索失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  };

  const handleStartBrowser = async () => {
    setBrowserBusy(true);
    setBrowserMsg("");
    try {
      const res = await startBossBrowser();
      setBrowserMsg(
        res.detail ||
          (res.reachable
            ? "专用浏览器已启动并就绪。首次使用请在弹出的 Chrome 窗口登录 BOSS 直聘，登录态会长期保留，之后即可直接检索。"
            : "已尝试启动，请在弹出的 Chrome 窗口登录 BOSS 直聘后点「重新检测」。"),
      );
      refreshStatus();
    } catch (exc) {
      setBrowserMsg(exc instanceof Error ? exc.message : "启动失败，请稍后重试");
    } finally {
      setBrowserBusy(false);
    }
  };

  const handleGenerate = async () => {
    if (!result) return;
    setGenError("");
    setFinishedSetId("");
    try {
      const taskId = await startJobGenerate({
        keyword: result.keyword,
        city: result.city,
        count,
        difficulty,
        withAnswer,
      });
      setProgress({
        generated: 0,
        total: count,
        currentDimension: "聚合分析岗位 JD",
        done: false,
        dropped: 0,
        setId: null,
      });
      for await (const p of streamGenerate(taskId)) {
        setProgress(p);
      }
      // 流结束：读最终态（done / error）
      setProgress((prev) => {
        if (prev?.error) {
          setGenError(prev.error);
        } else if (prev?.setId) {
          setFinishedSetId(prev.setId);
        }
        return prev;
      });
      getJobMap(result.keyword, result.city)
        .then(setJobMap)
        .catch(() => setJobMap(null));
    } catch (exc) {
      setGenError(exc instanceof Error ? exc.message : "生成任务发起失败，请稍后重试");
      setProgress(null);
    }
  };

  const handleAddToPipeline = async (job: JobCard) => {
    if (addingId) return;
    setAddingId(job.securityId);
    try {
      // 传 securityId + keyword/city：后端从检索缓存回填岗位信息 + 两阶段漏斗匹配分，并挂接该关键词「岗位市场题集」。
      // 注意用 ?? 而非 ||：查询城市为空时 result.city 为 ""，须原样传给后端以命中 sha256(关键词|城市) 缓存键，
      // 若回退成 job.city（岗位自身城市）会导致缓存键不一致、回填失败报「岗位名不能为空」。
      await addPipelineCard({
        securityId: job.securityId,
        keyword: result?.keyword ?? job.keyword,
        city: result?.city ?? job.city,
      });
      setAddedIds((prev) => new Set(prev).add(job.securityId));
      notify(`已将「${job.jobName}」加入求职看板，可在求职看板生成该岗位专属预测题`);
    } catch (exc) {
      notify(exc instanceof Error ? exc.message : "加入看板失败，请稍后重试");
    } finally {
      setAddingId(null);
    }
  };

  // 展开/收起岗位要求：展开时若未缓存则惰性拉全量 JD（不批量抓，规避限流/风控）
  const toggleRequirements = async (job: JobCard) => {
    const id = job.securityId;
    if (jdOpen.has(id)) {
      setJdOpen((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      return;
    }
    setJdOpen((prev) => new Set(prev).add(id));
    if (jdCache[id] !== undefined || jdLoading.has(id)) return;
    setJdLoading((prev) => new Set(prev).add(id));
    try {
      const res = await getJobDetail(id);
      const detail = (res.detail || {}) as Record<string, unknown>;
      let text = "";
      for (const key of ["jobDesc", "description", "content", "text", "desc"]) {
        const value = detail[key];
        if (typeof value === "string" && value.trim()) {
          text = value.trim();
          break;
        }
      }
      setJdCache((prev) => ({ ...prev, [id]: text }));
    } catch {
      setJdCache((prev) => ({ ...prev, [id]: "" }));
    } finally {
      setJdLoading((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  const blocked = status !== null && (!status.cliInstalled || !status.loggedIn);
  // 登录态 OK 但专用浏览器（CDP 9222）未连接：检索会 503，需先启动浏览器
  const browserDown = !blocked && status !== null && status.browserConnected === false;
  const percent =
    progress && progress.total > 0
      ? Math.min(100, Math.round((progress.generated / progress.total) * 100))
      : 0;
  // 两阶段漏斗产出的岗位匹配分（生成题库后由考点地图带回）
  const jobScores = jobMap?.jobScores ?? {};

  return (
    <main className="mx-auto max-w-3xl px-4 py-6">
      <header className="mb-4">
        <h1 className="text-xl font-semibold">岗位检索出题</h1>
        <p className="mt-1 text-sm text-muted">
          输入目标岗位，检索当前在招职位，聚合市场高频考点，生成三档题库与考点地图。
        </p>
      </header>

      {blocked && (
        <section className="mb-4 rounded-xl border border-warn/40 bg-warn/5 p-4">
          <h2 className="font-medium">首次使用需要关联 Boss 直聘登录态</h2>
          <p className="mt-1.5 text-sm text-muted">
            {status?.detail || "请在服务端完成 boss login 扫码登录。"}
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted">
            <li>本平台仅做只读检索（搜索岗位 / 查看职位详情），不代投递、不批量采集；</li>
            <li>登录凭据加密保存在服务端本机，检索结果缓存 1 天（次日同词检索会重新拉取最新岗位）；</li>
            <li>
              服务端执行 <code className="rounded bg-line/60 px-1">boss login</code> 后，用 Boss
              直聘 App 扫码，完成后点击下方按钮重试。
            </li>
          </ul>
          <button
            type="button"
            onClick={refreshStatus}
            className="mt-3 rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white"
          >
            我已扫码，重新检测
          </button>
        </section>
      )}

      {browserDown && (
        <section className="mb-4 rounded-xl border border-warn/40 bg-warn/5 p-4">
          <h2 className="font-medium">需要启动 Boss 专用浏览器</h2>
          <p className="mt-1.5 text-sm text-muted">
            {status?.browserHint ||
              "检索需一个已登录 BOSS 直聘的真实 Chrome（CDP 9222）。点击下方按钮启动专用浏览器，首次会打开登录页。"}
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted">
            <li>使用独立持久化 profile，登录一次后长期免登录，不影响你日常浏览器；</li>
            <li>启动后请在弹出的 Chrome 窗口完成 BOSS 直聘登录，再点「重新检测」；</li>
            <li>检索期间请保持该窗口开启（可最小化）。</li>
          </ul>
          {status?.browserStartCommand && (
            <p className="mt-2 text-xs text-muted">
              或手动运行：
              <code className="ml-1 rounded bg-line/60 px-1">{status.browserStartCommand}</code>
            </p>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={handleStartBrowser}
              disabled={browserBusy}
              className="rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              {browserBusy ? "启动中…" : "启动专用浏览器"}
            </button>
            <button
              type="button"
              onClick={refreshStatus}
              disabled={browserBusy}
              className="rounded-lg border border-line bg-card px-3 py-1.5 text-sm font-medium disabled:opacity-50"
            >
              我已登录，重新检测
            </button>
          </div>
        </section>
      )}

      {browserMsg && (
        <p className="mb-4 rounded-lg bg-brand/8 px-3 py-2 text-sm text-brand">
          {browserMsg}
        </p>
      )}

      <section className="rounded-xl border border-line bg-card p-4">
        <div className="flex flex-wrap gap-2">
          <input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !loading && handleSearch()}
            placeholder="目标岗位，如：产品经理 / Java 后端"
            className="min-w-0 flex-1 rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-brand"
          />
          <input
            value={city}
            onChange={(e) => setCity(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !loading && handleSearch()}
            placeholder="城市（可选）"
            className="w-32 rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-brand"
          />
          <button
            type="button"
            onClick={handleSearch}
            disabled={loading || !keyword.trim()}
            className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {loading ? "检索中…" : "搜索岗位"}
          </button>
        </div>
        {error && <p className="mt-2 text-sm text-danger">{error}</p>}
      </section>

      {result && (
        <>
          {/* 生成设置 chips（文档 3.5：题量 / 难度 / 附参考答案+解析） */}
          <section className="mt-4 rounded-xl border border-line bg-card p-4">
            <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted">题量</span>
                {COUNT_OPTIONS.map((n) => (
                  <button
                    key={n}
                    type="button"
                    onClick={() => setCount(n)}
                    className={`rounded-full px-3 py-1 text-xs font-medium ${
                      count === n ? "bg-brand text-white" : "bg-line/50 text-muted"
                    }`}
                  >
                    {n} 题
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted">难度</span>
                {DIFFICULTY_OPTIONS.map((d) => (
                  <button
                    key={d}
                    type="button"
                    onClick={() => setDifficulty(d)}
                    className={`rounded-full px-3 py-1 text-xs font-medium ${
                      difficulty === d ? "bg-brand text-white" : "bg-line/50 text-muted"
                    }`}
                  >
                    {d}
                  </button>
                ))}
              </div>
              <label className="flex cursor-pointer items-center gap-2 text-xs text-muted">
                <input
                  type="checkbox"
                  checked={withAnswer}
                  onChange={(e) => setWithAnswer(e.target.checked)}
                  className="accent-brand"
                />
                附参考答案 + 解析
              </label>
              <button
                type="button"
                onClick={handleGenerate}
                disabled={progress !== null && !progress.done}
                className="ml-auto rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                生成「{result.keyword}」市场题库
              </button>
            </div>
            {progress && !progress.done && (
              <div className="mt-3">
                <div className="flex items-center justify-between text-xs text-muted">
                  <span>
                    {progress.generated}/{progress.total} 题 · {progress.currentDimension}
                  </span>
                  <span>{percent}%</span>
                </div>
                <div className="mt-1 h-1.5 w-full rounded-full bg-line/70">
                  <div
                    className="h-full rounded-full bg-brand transition-all"
                    style={{ width: `${Math.max(percent, 2)}%` }}
                  />
                </div>
              </div>
            )}
            {genError && <p className="mt-2 text-sm text-danger">{genError}</p>}
            {finishedSetId && (
              <p className="mt-2 text-sm text-success">
                题库已生成，可到
                <Link href="/bank" className="mx-1 underline">
                  题库中心
                </Link>
                开始刷题（来源：岗位检索）
              </p>
            )}
          </section>

          {/* 考点地图：该岗位当前市场上最常被要求的技能 TOP 榜（文档 3.5） */}
          {jobMap && (
            <section className="mt-4 rounded-xl border border-line bg-card p-4">
              <div className="flex items-center justify-between">
                <h2 className="font-medium">考点地图 · {jobMap.keyword}</h2>
                {jobMap.degraded && (
                  <span className="rounded-full bg-line/50 px-2 py-0.5 text-xs text-muted">
                    规则版（聚合分析降级）
                  </span>
                )}
              </div>
              <p className="mt-1 text-xs text-muted">
                该岗位当前市场上最常被要求的 {jobMap.topSkills.length} 项技能
              </p>
              <ol className="mt-3 space-y-2">
                {jobMap.topSkills.map((skill, index) => (
                  <li key={skill.name} className="flex items-center gap-2">
                    <span className="w-5 shrink-0 text-right text-xs text-muted">{index + 1}</span>
                    <span className="w-28 shrink-0 truncate text-sm">{skill.name}</span>
                    <div className="h-1.5 flex-1 rounded-full bg-line/70">
                      <div
                        className="h-full rounded-full bg-brand"
                        style={{ width: `${Math.max(Math.min(skill.ratio, 100), 4)}%` }}
                      />
                    </div>
                    <span className="w-10 shrink-0 text-right text-xs text-muted">
                      {skill.ratio}%
                    </span>
                  </li>
                ))}
              </ol>
              {jobMap.duties.length > 0 && (
                <div className="mt-3">
                  <h3 className="text-xs text-muted">高频职责场景</h3>
                  <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm">
                    {jobMap.duties.map((duty) => (
                      <li key={duty}>{duty}</li>
                    ))}
                  </ul>
                </div>
              )}
              {jobMap.salaryInsight && (
                <p className="mt-3 rounded-lg bg-line/40 p-2 text-xs text-muted">
                  {jobMap.salaryInsight}
                </p>
              )}
              <div className="mt-3 flex flex-wrap gap-2">
                {TIER_META.map(({ key, label, cls }) => (
                  <span key={key} className={`rounded-full px-2.5 py-1 text-xs ${cls}`}>
                    {label} {jobMap.tiers[key].length} 个考点
                  </span>
                ))}
              </div>
            </section>
          )}

          <p className="mt-4 text-xs text-muted">
            共 {result.jobs.length} 个在招岗位（分层采样 · 覆盖不同薪资带）
            {result.cached ? " · 缓存结果（当日有效）" : " · 实时检索"}
          </p>
          {jobMap?.jobScores && (
            <p className="mt-1 text-xs text-muted">
              岗位卡「匹配 N%」为生成题库时两阶段漏斗对你简历画像/目标岗位的评分
              {jobMap.scoreDegraded ? "（本次评分降级为关键词匹配）" : ""}。
            </p>
          )}
          <ul className="mt-2 space-y-2">
            {result.jobs.map((job: JobCard) => (
              <li key={job.securityId} className="rounded-xl border border-line bg-card p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="truncate font-medium">{job.jobName}</h3>
                    <p className="mt-0.5 truncate text-sm text-muted">
                      {[job.brand, job.city, job.experience, job.education]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1">
                    <span className="text-sm font-semibold text-warn">{job.salary}</span>
                    {jobScores[job.securityId] && (
                      <span
                        className="rounded-full bg-brand/10 px-2 py-0.5 text-xs font-medium text-brand"
                        title={
                          jobScores[job.securityId].reason
                            ? `匹配理由：${jobScores[job.securityId].reason}`
                            : "与你的简历画像/目标岗位的匹配度"
                        }
                      >
                        匹配 {jobScores[job.securityId].score}%
                      </span>
                    )}
                  </div>
                </div>
                {job.skills.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {job.skills.map((skill) => (
                      <span
                        key={skill}
                        className="rounded-full bg-brand/8 px-2 py-0.5 text-xs text-brand"
                      >
                        {skill}
                      </span>
                    ))}
                  </div>
                )}
                {job.industry && <p className="mt-2 text-xs text-muted">{job.industry}</p>}
                {/* 岗位要求：折叠态显示检索自带片段；展开惰性拉全量 JD（单卡，不批量） */}
                <div className="mt-2">
                  {jdOpen.has(job.securityId) ? (
                    <div className="rounded-lg bg-line/40 p-2">
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-medium text-muted">岗位要求</span>
                        <button
                          type="button"
                          onClick={() => toggleRequirements(job)}
                          className="text-xs text-brand hover:underline"
                        >
                          收起
                        </button>
                      </div>
                      {jdLoading.has(job.securityId) ? (
                        <p className="mt-1 text-xs text-muted">加载中…</p>
                      ) : (
                        <p className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap text-xs text-muted">
                          {jdCache[job.securityId] ||
                            job.description ||
                            "（该岗位暂无详细要求描述）"}
                        </p>
                      )}
                    </div>
                  ) : (
                    <div>
                      {job.description && (
                        <p className="line-clamp-2 text-xs text-muted">{job.description}</p>
                      )}
                      <button
                        type="button"
                        onClick={() => toggleRequirements(job)}
                        className="text-xs text-brand hover:underline"
                      >
                        {job.description ? "展开岗位要求" : "查看岗位要求"}
                      </button>
                    </div>
                  )}
                </div>
                <div className="mt-3 flex justify-end border-t border-line pt-2.5">
                  <button
                    type="button"
                    onClick={() => handleAddToPipeline(job)}
                    disabled={addedIds.has(job.securityId) || addingId === job.securityId}
                    className="rounded-lg border border-line px-3 py-1 text-xs font-medium hover:border-brand hover:text-brand disabled:cursor-default disabled:border-success/40 disabled:text-success disabled:hover:border-success/40 disabled:hover:text-success"
                  >
                    {addedIds.has(job.securityId)
                      ? "✓ 已加入看板"
                      : addingId === job.securityId
                        ? "加入中…"
                        : "+ 加入求职看板"}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      {!result && !loading && (
        <div className="mt-4 rounded-xl border border-line bg-card p-4 text-sm text-muted">
          搜索结果示例：岗位名 + 薪资 + 公司 · 城市 · 经验 · 学历 + 业务标签。检索后可设置题量 /
          难度生成市场题库与考点地图。
        </div>
      )}

      <footer className="mt-6 border-t border-line pt-3 text-xs text-muted">
        {result?.dataSource || "数据来自 Boss 直聘 · 实时检索 · 缓存 1 天"} · 仅只读检索，不含投递与采集行为
      </footer>

      <p className="mt-3 text-center text-sm text-muted">
        需要 AI 能力？先到
        <Link href="/" className="mx-1 text-brand">
          首页
        </Link>
        上传简历生成专属题库
      </p>

      {toast && (
        <div className="fixed bottom-8 left-1/2 z-50 -translate-x-1/2 rounded-full bg-ink px-5 py-2.5 text-sm text-white shadow-lg">
          {toast}
        </div>
      )}
    </main>
  );
}
