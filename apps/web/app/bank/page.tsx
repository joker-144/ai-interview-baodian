"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { PageHeader, ProgressBar, SOURCE_TAG_CLASS } from "@/components/ui";
import { createSet, deleteSet, getProgress, getSets } from "@/lib/api";
import { SOURCE_LABEL, type QuestionSet, type QuestionSource } from "@/lib/types";

const FILTERS: { key: QuestionSource | "all"; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "resume", label: "简历生成" },
  { key: "job_search", label: "岗位检索" },
  { key: "jd_target", label: "JD定制" },
  { key: "mock_interview", label: "模拟面试" },
];

// 新建题集可选来源（排除「全部」）
const SOURCE_OPTIONS = FILTERS.slice(1);

export default function BankPage() {
  const [sets, setSets] = useState<QuestionSet[]>([]);
  const [progressMap, setProgressMap] = useState<Record<string, number>>({});
  const [filter, setFilter] = useState<QuestionSource | "all">("all");
  const [keyword, setKeyword] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newSource, setNewSource] = useState<QuestionSource>("resume");
  const [confirmDelete, setConfirmDelete] = useState<QuestionSet | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const refresh = async () => {
    const list = await getSets();
    setSets(list);
    const entries = await Promise.all(
      list.map(async (s) => [s.id, (await getProgress(s.id)).answeredCount] as const),
    );
    setProgressMap(Object.fromEntries(entries));
  };

  useEffect(() => {
    refresh();
  }, []);

  const notify = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 2400);
  };

  const onCreate = async () => {
    const title = newTitle.trim();
    if (!title) return;
    await createSet(title, newSource);
    setCreateOpen(false);
    setNewTitle("");
    setNewSource("resume");
    await refresh();
    notify(`已创建题集「${title}」`);
  };

  const onDelete = async () => {
    if (!confirmDelete) return;
    await deleteSet(confirmDelete.id);
    notify(`已删除题集「${confirmDelete.title}」`);
    setConfirmDelete(null);
    await refresh();
  };

  const filtered = useMemo(
    () =>
      sets.filter(
        (s) =>
          (filter === "all" || s.source === filter) &&
          (!keyword.trim() || s.title.includes(keyword.trim())),
      ),
    [sets, filter, keyword],
  );

  const totalQuestions = sets.reduce((sum, s) => sum + s.questionCount, 0);

  return (
    <div>
      <PageHeader
        title="题库中心"
        sub={`共 ${sets.length} 个题集 · ${totalQuestions} 道题 · AI 持续为你生成`}
      />

      {/* 搜索 + 新建 */}
      <div className="mb-4 flex gap-3">
        <div className="relative flex-1">
          <svg
            className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted"
            width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          >
            <circle cx="11" cy="11" r="7" />
            <path d="M21 21l-4.35-4.35" strokeLinecap="round" />
          </svg>
          <input
            className="input !pl-10"
            placeholder="搜索题集"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
          />
        </div>
        <button className="btn-primary shrink-0" onClick={() => setCreateOpen(true)}>
          新建题集
        </button>
      </div>

      {/* 来源筛选 */}
      <div className="mb-5 flex gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            className={`chip ${filter === f.key ? "chip-active" : ""}`}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* 题集卡片 */}
      {filtered.length === 0 ? (
        <div className="card py-16 text-center text-sm text-muted">
          没有匹配的题集，换个关键词或筛选条件试试
        </div>
      ) : (
        <div className="grid grid-cols-3 gap-4">
          {filtered.map((s) => {
            const done = progressMap[s.id] ?? 0;
            const finished = done >= s.questionCount && s.questionCount > 0;
            const started = done > 0;
            return (
              <div key={s.id} className="card flex flex-col p-5">
                <div className="flex items-center justify-between">
                  <span className={`tag ${SOURCE_TAG_CLASS[s.source]}`}>{SOURCE_LABEL[s.source]}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted">{s.updatedAt}</span>
                    <button
                      className="text-muted transition-colors hover:text-danger"
                      title={`删除题集「${s.title}」`}
                      onClick={() => setConfirmDelete(s)}
                    >
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                        <path d="M3 6h18M8 6V4h8v2m-9 0l1 14h8l1-14M10 10v7M14 10v7" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </button>
                  </div>
                </div>
                <h3 className="mt-3 text-base font-semibold">{s.title}</h3>
                <p className="mt-1 text-xs text-muted">{s.questionCount} 题</p>

                <div className="mt-4">
                  <ProgressBar value={done} max={s.questionCount} color={finished ? "bg-success" : "bg-brand"} />
                  <p className={`mt-1.5 text-xs ${finished ? "font-medium text-success" : "text-muted"}`}>
                    {finished ? `已完成 ${done}/${s.questionCount}` : started ? `完成 ${done}/${s.questionCount}` : "尚未开始"}
                  </p>
                </div>

                <div className="mt-4 flex-1" />
                {s.questionCount === 0 ? (
                  <span className="btn-secondary w-full cursor-not-allowed opacity-60">暂无题目</span>
                ) : (
                  <Link
                    href={`/practice/${s.id}`}
                    className={finished ? "btn-secondary w-full" : "btn-primary w-full"}
                  >
                    {finished ? "再刷一遍" : started ? "继续刷题" : "开始刷题"}
                  </Link>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* 新建题集弹窗 */}
      {createOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setCreateOpen(false)}
        >
          <div className="card w-96 p-6" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-base font-semibold">新建题集</h3>
            <p className="mt-1 text-xs text-muted">创建后可由 AI 引擎生成题目，或从题目详情页手动加入</p>

            <label className="mt-4 block text-sm text-muted">题集名称</label>
            <input
              className="input mt-1.5"
              placeholder="例如：产品经理面试冲刺"
              maxLength={40}
              value={newTitle}
              autoFocus
              onChange={(e) => setNewTitle(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && onCreate()}
            />

            <label className="mt-4 block text-sm text-muted">题集来源</label>
            <div className="mt-1.5 flex flex-wrap gap-2">
              {SOURCE_OPTIONS.map((o) => (
                <button
                  key={o.key}
                  className={`chip ${newSource === o.key ? "chip-active" : ""}`}
                  onClick={() => setNewSource(o.key as QuestionSource)}
                >
                  {o.label}
                </button>
              ))}
            </div>

            <div className="mt-6 flex gap-2">
              <button className="btn-secondary flex-1" onClick={() => setCreateOpen(false)}>
                取消
              </button>
              <button
                className="btn-primary flex-1 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={!newTitle.trim()}
                onClick={onCreate}
              >
                创建
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 删除确认弹窗 */}
      {confirmDelete && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setConfirmDelete(null)}
        >
          <div className="card w-80 p-6" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-base font-semibold">删除题集</h3>
            <p className="mt-2 text-sm leading-6 text-muted">
              确认删除「{confirmDelete.title}」？该题集的刷题进度与相关错题将一并移除，不可恢复。
            </p>
            <div className="mt-5 flex gap-2">
              <button className="btn-secondary flex-1" onClick={() => setConfirmDelete(null)}>
                取消
              </button>
              <button
                className="btn-primary flex-1 !bg-danger !text-white"
                onClick={onDelete}
              >
                删除
              </button>
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
