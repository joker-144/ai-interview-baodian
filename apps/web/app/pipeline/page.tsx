"use client";

/**
 * P? 求职看板（四期，job_pipeline 四列状态机）。
 *
 * 已投递 → 笔试 → 面试 → Offer 四列；卡片可由岗位检索「加入看板」自动带入两阶段漏斗
 * 匹配分并挂接该关键词「岗位市场题集」（显示练习进度、去练习直达 /practice/{setId}），
 * 也可手动录入外部平台岗位（智联/前程无忧/猎聘/其他=能力边界降级，无自动回填）。
 *
 * 单岗位专属预测题（四期增补，引擎 C 子集）：zhipin 卡上「生成专属预测题」按设置
 * （题量/难度/附答案）触发 source=jd_target 出题，SSE 卡级进度，完成后题集自动挂接本卡。
 *
 * 合规红线：本页是纯本地状态机——状态流转与 HR 回复备注均由用户手动维护，平台不代投递、
 * 不打招呼、不监听 HR。面试日期临近时后端惰性补一条 interview_prep 复习提醒（当日去重）。
 */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  addPipelineCard,
  deletePipelineCard,
  getPipeline,
  getPipelineTrash,
  purgePipelineCard,
  restorePipelineCard,
  startJobDetailGenerate,
  streamGenerate,
  updatePipelineCard,
} from "@/lib/api";
import type {
  GenerateProgress,
  PipelineBoard,
  PipelineCard,
  PipelinePlatform,
  PipelineStage,
} from "@/lib/types";
import { ProgressBar, StatCard } from "@/components/ui";

const STAGES: PipelineStage[] = ["applied", "written", "interview", "offer"];

const STAGE_META: Record<PipelineStage, { label: string; dot: string; head: string }> = {
  applied: { label: "已投递", dot: "bg-brand", head: "text-brand" },
  written: { label: "笔试", dot: "bg-warn", head: "text-warn" },
  interview: { label: "面试", dot: "bg-ai", head: "text-ai" },
  offer: { label: "Offer", dot: "bg-success", head: "text-success" },
};

const PLATFORM_META: Record<PipelinePlatform, { label: string; cls: string }> = {
  zhipin: { label: "BOSS直聘", cls: "bg-brand-light text-brand" },
  zhilian: { label: "智联招聘", cls: "bg-orange-50 text-warn" },
  "51job": { label: "前程无忧", cls: "bg-green-50 text-success" },
  liepin: { label: "猎聘", cls: "bg-purple-50 text-ai" },
  manual: { label: "手动录入", cls: "bg-line/60 text-muted" },
};

/** 手动录入可选平台（外部平台=降级手动卡；zhipin 卡走岗位检索「加入看板」自动回填） */
const MANUAL_PLATFORMS: PipelinePlatform[] = ["zhilian", "51job", "liepin", "manual"];

/** 单岗位专属出题设置项（默认 30 题：单 JD 素材少于市场级） */
const GEN_COUNT_OPTIONS = [20, 30, 50];
const GEN_DIFFICULTY_OPTIONS = ["混合", "L1", "L2", "L3"];

/** 单张看板卡（列内渲染）：匹配分 / 平台 / 面试日期 / HR 备注 / 题集进度 / 专属出题 / 流转·编辑·回收 */
function CardView({
  card,
  generating,
  genProgress,
  genLocked,
  onGenerate,
  onMove,
  onEdit,
  onTrash,
}: {
  card: PipelineCard;
  /** 本卡正在生成专属预测题（显示卡级进度、禁用按钮防重） */
  generating: boolean;
  genProgress: GenerateProgress | null;
  /** 任一卡在生成中（单飞：其余卡生成按钮同步禁用） */
  genLocked: boolean;
  onGenerate: (card: PipelineCard) => void;
  onMove: (card: PipelineCard, stage: PipelineStage) => void;
  onEdit: (card: PipelineCard) => void;
  onTrash: (card: PipelineCard) => void;
}) {
  const idx = STAGES.indexOf(card.stage);
  const prev = idx > 0 ? STAGES[idx - 1] : null;
  const next = idx < STAGES.length - 1 ? STAGES[idx + 1] : null;
  const plat = PLATFORM_META[card.platform] ?? PLATFORM_META.manual;
  // 已有题（setId + 题量>0）→「去练习 / 重新生成」；仅 zhipin 卡可生成（手动卡降级）
  const hasQuestions = Boolean(card.setId) && card.practice.total > 0;
  const percent = genProgress
    ? Math.min(100, Math.round((genProgress.generated / Math.max(genProgress.total, 1)) * 100))
    : 0;
  return (
    <article className="card p-3">
      <div className="flex items-start justify-between gap-2">
        <h3 className="min-w-0 truncate text-sm font-medium" title={card.jobName}>
          {card.jobName}
        </h3>
        {card.matchScore !== null && (
          <span className="shrink-0 rounded-full bg-brand/10 px-2 py-0.5 text-xs font-medium text-brand">
            匹配 {card.matchScore}%
          </span>
        )}
      </div>
      {(card.brand || card.city) && (
        <p className="mt-0.5 truncate text-xs text-muted">
          {[card.brand, card.city].filter(Boolean).join(" · ")}
        </p>
      )}
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        {card.salary && <span className="text-xs font-semibold text-warn">{card.salary}</span>}
        <span className={`tag ${plat.cls}`}>{plat.label}</span>
      </div>
      {card.interviewAt && (
        <p
          className={`mt-1.5 text-xs ${
            card.stage === "interview" ? "font-medium text-ai" : "text-muted"
          }`}
        >
          面试：{card.interviewAt}
        </p>
      )}
      {card.note && (
        <p
          className="mt-1.5 line-clamp-2 rounded-btn bg-line/40 px-2 py-1 text-xs text-muted"
          title={card.note}
        >
          {card.note}
        </p>
      )}
      {hasQuestions ? (
        <div className="mt-2">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted">
              定向题库 {card.practice.done}/{card.practice.total}
            </span>
            <Link
              href={`/practice/${card.setId}`}
              className="font-medium text-brand hover:underline"
            >
              去练习
            </Link>
          </div>
          <div className="mt-1">
            <ProgressBar value={card.practice.done} max={card.practice.total} />
          </div>
        </div>
      ) : !card.securityId ? (
        <p className="mt-2 text-xs text-muted/70">未挂接定向题库</p>
      ) : null}
      {card.securityId &&
        (generating && genProgress ? (
          <div className="mt-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted">正在生成专属预测题…</span>
              <span className="font-medium text-brand">{percent}%</span>
            </div>
            <div className="mt-1">
              <ProgressBar value={genProgress.generated} max={genProgress.total} />
            </div>
            <p className="mt-1 truncate text-xs text-muted/80" title={genProgress.currentDimension}>
              当前维度：{genProgress.currentDimension}
            </p>
          </div>
        ) : (
          <button
            type="button"
            disabled={genLocked}
            onClick={() => onGenerate(card)}
            title={genLocked ? "已有岗位正在生成，请稍候" : "基于该岗位 JD 预测面试问题"}
            className="mt-2 w-full rounded-btn border border-brand px-2 py-1.5 text-xs font-medium text-brand hover:bg-brand-light disabled:cursor-not-allowed disabled:opacity-40"
          >
            {hasQuestions ? "重新生成专属预测题" : "生成专属预测题"}
          </button>
        ))}
      <div className="mt-2.5 flex items-center gap-1 border-t border-line pt-2">
        <button
          type="button"
          disabled={!prev}
          onClick={() => prev && onMove(card, prev)}
          title={prev ? `移到「${STAGE_META[prev].label}」` : "已是首阶段"}
          className="rounded-btn border border-line px-2 py-1 text-xs disabled:opacity-30 enabled:hover:border-brand enabled:hover:text-brand"
        >
          ‹
        </button>
        <button
          type="button"
          disabled={!next}
          onClick={() => next && onMove(card, next)}
          title={next ? `移到「${STAGE_META[next].label}」` : "已是末阶段"}
          className="rounded-btn border border-line px-2 py-1 text-xs disabled:opacity-30 enabled:hover:border-brand enabled:hover:text-brand"
        >
          ›
        </button>
        <button
          type="button"
          onClick={() => onEdit(card)}
          className="ml-auto rounded-btn border border-line px-2 py-1 text-xs hover:border-brand hover:text-brand"
        >
          编辑
        </button>
        <button
          type="button"
          onClick={() => onTrash(card)}
          className="rounded-btn border border-line px-2 py-1 text-xs hover:border-danger hover:text-danger"
        >
          回收站
        </button>
      </div>
    </article>
  );
}

export default function PipelinePage() {
  const [board, setBoard] = useState<PipelineBoard | null>(null);
  const [trashCards, setTrashCards] = useState<PipelineCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 回收站抽屉
  const [trashOpen, setTrashOpen] = useState(false);
  // 手动录入弹窗
  const [manualOpen, setManualOpen] = useState(false);
  const [mJobName, setMJobName] = useState("");
  const [mBrand, setMBrand] = useState("");
  const [mCity, setMCity] = useState("");
  const [mSalary, setMSalary] = useState("");
  const [mPlatform, setMPlatform] = useState<PipelinePlatform>("manual");
  const [mStage, setMStage] = useState<PipelineStage>("applied");
  const [mInterview, setMInterview] = useState("");
  const [mNote, setMNote] = useState("");
  // 编辑弹窗
  const [editCard, setEditCard] = useState<PipelineCard | null>(null);
  const [eStage, setEStage] = useState<PipelineStage>("applied");
  const [eInterview, setEInterview] = useState("");
  const [eNote, setENote] = useState("");
  // 单岗位专属出题（引擎 C 子集）：设置弹窗 + 卡级 SSE 进度（单飞防重）
  const [genTarget, setGenTarget] = useState<PipelineCard | null>(null);
  const [gCount, setGCount] = useState(30);
  const [gDifficulty, setGDifficulty] = useState("混合");
  const [gAnswer, setGAnswer] = useState(true);
  const [genCardId, setGenCardId] = useState("");
  const [genProgress, setGenProgress] = useState<GenerateProgress | null>(null);

  const notify = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 2400);
  };

  const load = useCallback(async () => {
    try {
      const [b, t] = await Promise.all([getPipeline(), getPipelineTrash()]);
      setBoard(b);
      setTrashCards(t.cards);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载求职看板失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const moveStage = async (card: PipelineCard, stage: PipelineStage) => {
    if (stage === card.stage || busy) return;
    setBusy(true);
    try {
      await updatePipelineCard(card.id, { stage });
      await load();
    } catch (e) {
      notify(e instanceof Error ? e.message : "流转失败");
    } finally {
      setBusy(false);
    }
  };

  const softDelete = async (card: PipelineCard) => {
    if (busy) return;
    setBusy(true);
    try {
      await deletePipelineCard(card.id);
      await load();
      notify("已移入回收站");
    } catch (e) {
      notify(e instanceof Error ? e.message : "移入回收站失败");
    } finally {
      setBusy(false);
    }
  };

  const restore = async (card: PipelineCard) => {
    if (busy) return;
    setBusy(true);
    try {
      await restorePipelineCard(card.id);
      await load();
      notify("已还原到看板");
    } catch (e) {
      notify(e instanceof Error ? e.message : "还原失败");
    } finally {
      setBusy(false);
    }
  };

  const purge = async (card: PipelineCard) => {
    if (busy) return;
    setBusy(true);
    try {
      await purgePipelineCard(card.id);
      await load();
      notify("已彻底删除");
    } catch (e) {
      notify(e instanceof Error ? e.message : "删除失败");
    } finally {
      setBusy(false);
    }
  };

  const openManual = () => {
    setMJobName("");
    setMBrand("");
    setMCity("");
    setMSalary("");
    setMPlatform("manual");
    setMStage("applied");
    setMInterview("");
    setMNote("");
    setManualOpen(true);
  };

  const submitManual = async () => {
    if (!mJobName.trim() || busy) return;
    setBusy(true);
    try {
      await addPipelineCard({
        jobName: mJobName.trim(),
        brand: mBrand.trim(),
        city: mCity.trim(),
        salary: mSalary.trim(),
        platform: mPlatform,
        stage: mStage,
        interviewAt: mInterview,
        note: mNote.trim(),
      });
      setManualOpen(false);
      await load();
      notify("已加入求职看板");
    } catch (e) {
      notify(e instanceof Error ? e.message : "加入看板失败");
    } finally {
      setBusy(false);
    }
  };

  const openEdit = (card: PipelineCard) => {
    setEditCard(card);
    setEStage(card.stage);
    setEInterview(card.interviewAt);
    setENote(card.note);
  };

  const submitEdit = async () => {
    if (!editCard || busy) return;
    setBusy(true);
    try {
      await updatePipelineCard(editCard.id, {
        stage: eStage,
        interviewAt: eInterview,
        note: eNote.trim(),
      });
      setEditCard(null);
      await load();
      notify("已保存");
    } catch (e) {
      notify(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  const openGenerate = (card: PipelineCard) => {
    if (genCardId) return;
    setGCount(30);
    setGDifficulty("混合");
    setGAnswer(true);
    setGenTarget(card);
  };

  const submitGenerate = async () => {
    const card = genTarget;
    if (!card || genCardId) return;
    setGenTarget(null);
    setGenCardId(card.id);
    setGenProgress({
      generated: 0,
      total: gCount,
      currentDimension: "读取岗位 JD",
      done: false,
      dropped: 0,
      setId: null,
    });
    try {
      const taskId = await startJobDetailGenerate({
        securityId: card.securityId,
        keyword: card.keyword,
        city: card.city,
        count: gCount,
        difficulty: gDifficulty,
        withAnswer: gAnswer,
      });
      let last: GenerateProgress | null = null;
      for await (const p of streamGenerate(taskId)) {
        last = p;
        setGenProgress(p);
      }
      // 流结束：刷新看板（setId 挂接、练习进度生效），失败则直出后端 detail
      await load();
      if (last?.error) {
        notify(last.error);
      } else {
        notify("已生成专属题库");
      }
    } catch (exc) {
      notify(exc instanceof Error ? exc.message : "生成任务发起失败，请稍后重试");
    } finally {
      setGenCardId("");
      setGenProgress(null);
    }
  };

  if (!board) {
    return (
      <div className="mt-10 text-center text-sm text-muted">
        {error ? <span className="text-warn">{error}</span> : "加载中…"}
      </div>
    );
  }

  const { stats } = board;

  return (
    <div>
      <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">求职看板</h1>
          <p className="mt-1 text-sm text-muted">
            已投递 → 笔试 → 面试 → Offer 四列跟进；状态与 HR 回复均由你手动记录，平台不代投递、不打招呼。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn-primary" onClick={openManual}>
            + 手动录入
          </button>
          <Link href="/jobs" className="btn-secondary">
            从岗位检索添加
          </Link>
          <button type="button" className="btn-secondary" onClick={() => setTrashOpen(true)}>
            回收站{trashCards.length > 0 ? ` (${trashCards.length})` : ""}
          </button>
        </div>
      </header>

      {/* 趋势统计条 */}
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard value={stats.total} label="看板岗位" />
        <StatCard value={stats.newThisWeek} label="本周新增" tone="brand" />
        <StatCard value={stats.pendingInterview} label="待面试" tone="warn" />
        <StatCard
          value={stats.avgMatchScore === null ? "—" : `${stats.avgMatchScore}%`}
          label="平均匹配分"
          tone="success"
        />
      </div>

      {error && <p className="mb-3 text-sm text-danger">{error}</p>}

      {/* 四列看板：移动端横向滚动，md 两列，xl 四列 */}
      <div className="flex gap-4 overflow-x-auto pb-2 md:grid md:grid-cols-2 md:overflow-visible md:pb-0 xl:grid-cols-4">
        {STAGES.map((stage) => {
          const cards = board.columns[stage] ?? [];
          const meta = STAGE_META[stage];
          return (
            <section key={stage} className="flex w-72 shrink-0 flex-col md:w-auto md:shrink">
              <div className="mb-2 flex items-center gap-2">
                <span className={`h-2 w-2 rounded-full ${meta.dot}`} />
                <h2 className={`text-sm font-semibold ${meta.head}`}>{meta.label}</h2>
                <span className="tag bg-line/60 text-muted">{cards.length}</span>
              </div>
              <div className="flex flex-col gap-2">
                {cards.length === 0 ? (
                  <div className="rounded-card border border-dashed border-line px-3 py-6 text-center text-xs text-muted">
                    暂无岗位
                  </div>
                ) : (
                  cards.map((card) => (
                    <CardView
                      key={card.id}
                      card={card}
                      generating={genCardId === card.id}
                      genProgress={genCardId === card.id ? genProgress : null}
                      genLocked={genCardId !== ""}
                      onGenerate={openGenerate}
                      onMove={moveStage}
                      onEdit={openEdit}
                      onTrash={softDelete}
                    />
                  ))
                )}
              </div>
            </section>
          );
        })}
      </div>

      <footer className="mt-6 border-t border-line pt-3 text-xs text-muted">
        求职看板为本地状态管理 · 岗位信息来自你的检索缓存或手动录入 · 仅只读检索，不含投递与采集行为
      </footer>

      {/* 手动录入弹窗（外部平台=降级手动卡，无自动回填） */}
      {manualOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
          onClick={() => !busy && setManualOpen(false)}
        >
          <div className="card max-h-[88vh] w-full max-w-md overflow-y-auto p-6" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-base font-semibold">手动录入岗位</h3>
            <p className="mt-1 text-xs text-muted">
              用于记录智联 / 前程无忧 / 猎聘等外部平台岗位；手动卡不自动回填匹配分与题库。
            </p>

            <label className="mt-4 block text-sm text-muted">岗位名 *</label>
            <input className="input mt-1.5" placeholder="例如：后端工程师" maxLength={80} value={mJobName} autoFocus onChange={(e) => setMJobName(e.target.value)} />

            <div className="mt-3 grid grid-cols-2 gap-3">
              <div>
                <label className="block text-sm text-muted">公司</label>
                <input className="input mt-1.5" placeholder="公司名称" maxLength={80} value={mBrand} onChange={(e) => setMBrand(e.target.value)} />
              </div>
              <div>
                <label className="block text-sm text-muted">城市</label>
                <input className="input mt-1.5" placeholder="例如：杭州" maxLength={32} value={mCity} onChange={(e) => setMCity(e.target.value)} />
              </div>
            </div>

            <label className="mt-3 block text-sm text-muted">薪资</label>
            <input className="input mt-1.5" placeholder="例如：20-30K" maxLength={32} value={mSalary} onChange={(e) => setMSalary(e.target.value)} />

            <label className="mt-4 block text-sm text-muted">来源平台</label>
            <div className="mt-1.5 flex flex-wrap gap-2">
              {MANUAL_PLATFORMS.map((p) => (
                <button key={p} type="button" className={`chip ${mPlatform === p ? "chip-active" : ""}`} onClick={() => setMPlatform(p)}>
                  {PLATFORM_META[p].label}
                </button>
              ))}
            </div>

            <label className="mt-4 block text-sm text-muted">阶段</label>
            <div className="mt-1.5 flex flex-wrap gap-2">
              {STAGES.map((s) => (
                <button key={s} type="button" className={`chip ${mStage === s ? "chip-active" : ""}`} onClick={() => setMStage(s)}>
                  {STAGE_META[s].label}
                </button>
              ))}
            </div>

            <label className="mt-4 block text-sm text-muted">面试日期（可选）</label>
            <input type="date" className="input mt-1.5" value={mInterview} onChange={(e) => setMInterview(e.target.value)} />

            <label className="mt-4 block text-sm text-muted">HR 回复 / 跟进备注（可选）</label>
            <textarea className="input mt-1.5 min-h-[72px] resize-y" placeholder="记录沟通进展、面试安排等" maxLength={2000} value={mNote} onChange={(e) => setMNote(e.target.value)} />

            <div className="mt-6 flex gap-2">
              <button type="button" className="btn-secondary flex-1" disabled={busy} onClick={() => setManualOpen(false)}>
                取消
              </button>
              <button type="button" className="btn-primary flex-1 disabled:cursor-not-allowed disabled:opacity-50" disabled={!mJobName.trim() || busy} onClick={submitManual}>
                添加
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 编辑弹窗：流转阶段 / 面试日期 / HR 备注 */}
      {editCard && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
          onClick={() => !busy && setEditCard(null)}
        >
          <div className="card w-full max-w-md p-6" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-base font-semibold">编辑 · {editCard.jobName}</h3>

            <label className="mt-4 block text-sm text-muted">阶段</label>
            <div className="mt-1.5 flex flex-wrap gap-2">
              {STAGES.map((s) => (
                <button key={s} type="button" className={`chip ${eStage === s ? "chip-active" : ""}`} onClick={() => setEStage(s)}>
                  {STAGE_META[s].label}
                </button>
              ))}
            </div>

            <label className="mt-4 block text-sm text-muted">面试日期</label>
            <input type="date" className="input mt-1.5" value={eInterview} onChange={(e) => setEInterview(e.target.value)} />
            <p className="mt-1 text-xs text-muted">设为未来 3 天内的日期，将在看板生成面试临近复习提醒。</p>

            <label className="mt-4 block text-sm text-muted">HR 回复 / 跟进备注</label>
            <textarea className="input mt-1.5 min-h-[88px] resize-y" maxLength={2000} value={eNote} onChange={(e) => setENote(e.target.value)} />

            <div className="mt-6 flex gap-2">
              <button type="button" className="btn-secondary flex-1" disabled={busy} onClick={() => setEditCard(null)}>
                取消
              </button>
              <button type="button" className="btn-primary flex-1 disabled:opacity-50" disabled={busy} onClick={submitEdit}>
                保存
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 单岗位专属出题设置弹窗（zhipin 卡：题量/难度/附答案 → SSE 卡级进度） */}
      {genTarget && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
          onClick={() => setGenTarget(null)}
        >
          <div className="card w-full max-w-md p-6" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-base font-semibold">生成专属预测题</h3>
            <p className="mt-1 text-xs text-muted">
              基于「{genTarget.jobName}」的岗位 JD 预测该岗位面试最可能问的问题，生成后自动挂接到本卡。
            </p>

            <label className="mt-4 block text-sm text-muted">题量</label>
            <div className="mt-1.5 flex flex-wrap gap-2">
              {GEN_COUNT_OPTIONS.map((n) => (
                <button key={n} type="button" className={`chip ${gCount === n ? "chip-active" : ""}`} onClick={() => setGCount(n)}>
                  {n} 题
                </button>
              ))}
            </div>

            <label className="mt-4 block text-sm text-muted">难度</label>
            <div className="mt-1.5 flex flex-wrap gap-2">
              {GEN_DIFFICULTY_OPTIONS.map((d) => (
                <button key={d} type="button" className={`chip ${gDifficulty === d ? "chip-active" : ""}`} onClick={() => setGDifficulty(d)}>
                  {d}
                </button>
              ))}
            </div>

            <label className="mt-4 flex cursor-pointer items-center gap-2 text-sm text-muted">
              <input type="checkbox" checked={gAnswer} onChange={(e) => setGAnswer(e.target.checked)} />
              附参考答案（口头作答要点）
            </label>

            <div className="mt-6 flex gap-2">
              <button type="button" className="btn-secondary flex-1" onClick={() => setGenTarget(null)}>
                取消
              </button>
              <button type="button" className="btn-primary flex-1" onClick={submitGenerate}>
                开始生成
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 回收站抽屉：还原 / 彻底删除 */}
      {trashOpen && (
        <div
          className="fixed inset-0 z-50 flex items-end justify-center bg-ink/30 sm:items-center"
          onClick={() => setTrashOpen(false)}
        >
          <div
            className="card flex max-h-[82vh] w-full max-w-lg flex-col rounded-b-none p-6 sm:rounded-card"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-base font-semibold">回收站</h3>
              <button type="button" className="text-sm text-muted hover:text-ink" onClick={() => setTrashOpen(false)}>
                关闭
              </button>
            </div>
            <div className="flex-1 overflow-y-auto">
              {trashCards.length === 0 ? (
                <p className="py-10 text-center text-sm text-muted">回收站为空</p>
              ) : (
                <ul className="space-y-2">
                  {trashCards.map((card) => {
                    const plat = PLATFORM_META[card.platform] ?? PLATFORM_META.manual;
                    return (
                      <li key={card.id} className="flex items-center gap-3 rounded-btn border border-line px-3 py-2">
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-medium">{card.jobName}</p>
                          <p className="mt-0.5 truncate text-xs text-muted">
                            {[card.brand, card.city].filter(Boolean).join(" · ") || STAGE_META[card.stage].label}
                          </p>
                        </div>
                        <span className={`tag shrink-0 ${plat.cls}`}>{plat.label}</span>
                        <button type="button" disabled={busy} className="shrink-0 rounded-btn border border-line px-2 py-1 text-xs hover:border-brand hover:text-brand disabled:opacity-40" onClick={() => restore(card)}>
                          还原
                        </button>
                        <button type="button" disabled={busy} className="shrink-0 rounded-btn border border-line px-2 py-1 text-xs hover:border-danger hover:text-danger disabled:opacity-40" onClick={() => purge(card)}>
                          彻底删除
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}

      {toast && (
        <div className="fixed bottom-8 left-1/2 z-50 -translate-x-1/2 rounded-full bg-ink px-5 py-2.5 text-sm text-white shadow-lg">
          {toast}
        </div>
      )}
    </div>
  );
}
