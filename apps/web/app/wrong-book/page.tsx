"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { PageHeader, StatCard } from "@/components/ui";
import {
  getQuestion,
  getReviewQueue,
  getSet,
  getWrongBook,
  getWrongStats,
  updateWrongReason,
} from "@/lib/api";
import {
  WRONG_REASON_LABEL,
  type Question,
  type WrongItem,
  type WrongReason,
} from "@/lib/types";

const REASON_FILTERS: { key: WrongReason | "all"; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "concept", label: "概念不清" },
  { key: "misread", label: "审题失误" },
  { key: "blind_spot", label: "知识盲区" },
];

const REASON_TAG_CLASS: Record<WrongReason, string> = {
  concept: "bg-brand-light text-brand",
  misread: "bg-orange-50 text-warn",
  blind_spot: "bg-purple-50 text-ai",
};

interface WrongRow extends WrongItem {
  stem: string;
  setTitle: string;
}

export default function WrongBookPage() {
  const [rows, setRows] = useState<WrongRow[]>([]);
  const [stats, setStats] = useState({ pending: 0, dueToday: 0, mastered: 0 });
  const [queue, setQueue] = useState<{ label: string; count: number }[]>([]);
  const [filter, setFilter] = useState<WrongReason | "all">("all");
  const [editingReason, setEditingReason] = useState<string | null>(null);

  const load = async () => {
    const [items, s, q] = await Promise.all([getWrongBook(), getWrongStats(), getReviewQueue()]);
    const hydrated = await Promise.all(
      items.map(async (w): Promise<WrongRow> => {
        const [question, set] = await Promise.all([getQuestion(w.questionId), getSet(w.setId)]);
        return { ...w, stem: question?.stem ?? "题目已下线", setTitle: set?.title ?? "未知题集" };
      }),
    );
    setRows(hydrated);
    setStats(s);
    setQueue(q);
  };

  useEffect(() => {
    load();
  }, []);

  const filtered = useMemo(
    () => (filter === "all" ? rows : rows.filter((r) => r.reason === filter)),
    [rows, filter],
  );

  const changeReason = async (questionId: string, reason: WrongReason) => {
    await updateWrongReason(questionId, reason);
    setEditingReason(null);
    await load();
  };

  return (
    <div>
      <PageHeader title="错题本" sub="按艾宾浩斯遗忘曲线智能安排复习，攻克每一个薄弱点" />

      <div className="grid grid-cols-3 gap-5">
        {/* 左侧：统计 + 筛选 + 错题卡 */}
        <div className="col-span-2">
          <div className="grid grid-cols-3 gap-3">
            <StatCard value={stats.pending} label="待复习" tone="warn" />
            <StatCard value={stats.dueToday} label="今日到期" tone="danger" />
            <StatCard value={stats.mastered} label="已掌握" tone="success" />
          </div>

          <div className="mb-4 mt-5 flex gap-2">
            {REASON_FILTERS.map((f) => (
              <button
                key={f.key}
                className={`chip ${filter === f.key ? "chip-active" : ""}`}
                onClick={() => setFilter(f.key)}
              >
                {f.label}
              </button>
            ))}
          </div>

          {filtered.length === 0 ? (
            <div className="card py-16 text-center">
              <p className="text-sm text-muted">太棒了，当前分类下没有待复习错题</p>
              <Link href="/bank" className="mt-3 inline-block text-sm font-medium text-brand">
                去刷一套题检验一下 →
              </Link>
            </div>
          ) : (
            <div className="space-y-3">
              {filtered.map((r) => (
                <div key={r.questionId} className="card p-5">
                  <div className="flex items-center gap-2">
                    <div className="relative">
                      <button
                        className={`tag ${REASON_TAG_CLASS[r.reason]}`}
                        title="点击修改错因"
                        onClick={() =>
                          setEditingReason(editingReason === r.questionId ? null : r.questionId)
                        }
                      >
                        {WRONG_REASON_LABEL[r.reason]} ▾
                      </button>
                      {editingReason === r.questionId && (
                        <div className="card absolute left-0 top-7 z-10 w-36 p-2 shadow-lg">
                          {(Object.keys(WRONG_REASON_LABEL) as WrongReason[]).map((reason) => (
                            <button
                              key={reason}
                              className={`block w-full rounded-btn px-3 py-1.5 text-left text-sm hover:bg-bg ${
                                reason === r.reason ? "font-medium text-brand" : ""
                              }`}
                              onClick={() => changeReason(r.questionId, reason)}
                            >
                              {WRONG_REASON_LABEL[reason]}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                    <span className="text-xs text-muted">{r.setTitle}</span>
                  </div>

                  <p className="mt-3 text-sm font-medium leading-6">{r.stem}</p>

                  <div className="mt-3 flex items-center text-xs text-muted">
                    <span>
                      错误 {r.wrongCount} 次 · 最近错误 {r.lastWrongAt} · 下次复习{" "}
                      <span className={r.nextReviewLabel === "今天" ? "font-medium text-danger" : ""}>
                        {r.nextReviewLabel}
                      </span>
                    </span>
                    <div className="ml-auto flex gap-2">
                      <Link href={`/question/${r.questionId}`} className="btn-secondary !px-3 !py-1 text-xs">
                        看解析
                      </Link>
                      <Link href="/practice/review" className="btn-primary !px-3 !py-1 text-xs">
                        立即复习
                      </Link>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 右侧：艾宾浩斯复习计划 */}
        <aside className="card h-fit p-5">
          <h2 className="text-base font-semibold">艾宾浩斯复习计划</h2>
          <p className="mt-1 text-xs text-muted">复习通过后自动归档「已掌握」</p>
          <ul className="mt-4 space-y-2.5">
            {queue.map((q, i) => (
              <li key={q.label} className="flex items-center justify-between rounded-btn bg-bg px-4 py-2.5">
                <span className={`text-sm ${i === 0 ? "font-medium text-ink" : "text-muted"}`}>{q.label}</span>
                <span className={`text-sm font-semibold ${i === 0 ? "text-danger" : "text-ink"}`}>{q.count} 题</span>
              </li>
            ))}
          </ul>
          {queue[0]?.count ? (
            <Link href="/practice/review" className="btn-primary mt-5 w-full">
              开始今日复习（{queue[0].count} 题）
            </Link>
          ) : (
            <p className="mt-5 rounded-btn bg-bg px-4 py-3 text-center text-sm text-muted">
              今日暂无到期错题，去刷题检验一下吧
            </p>
          )}
        </aside>
      </div>
    </div>
  );
}
