"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { PageHeader, ProgressBar, SOURCE_TAG_CLASS } from "@/components/ui";
import { getProgress, getSets } from "@/lib/api";
import { SOURCE_LABEL, type QuestionSet, type QuestionSource } from "@/lib/types";

const FILTERS: { key: QuestionSource | "all"; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "resume", label: "简历生成" },
  { key: "job_search", label: "岗位检索" },
  { key: "jd_target", label: "JD定制" },
  { key: "mock_interview", label: "模拟面试" },
];

export default function BankPage() {
  const [sets, setSets] = useState<QuestionSet[]>([]);
  const [progressMap, setProgressMap] = useState<Record<string, number>>({});
  const [filter, setFilter] = useState<QuestionSource | "all">("all");
  const [keyword, setKeyword] = useState("");

  useEffect(() => {
    getSets().then(async (list) => {
      setSets(list);
      const entries = await Promise.all(
        list.map(async (s) => [s.id, (await getProgress(s.id)).answeredCount] as const),
      );
      setProgressMap(Object.fromEntries(entries));
    });
  }, []);

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
        <button className="btn-primary shrink-0">新建题集</button>
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
                  <span className="text-xs text-muted">{s.updatedAt}</span>
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
                <Link
                  href={`/practice/${s.id}`}
                  className={finished ? "btn-secondary w-full" : "btn-primary w-full"}
                >
                  {finished ? "再刷一遍" : started ? "继续刷题" : "开始刷题"}
                </Link>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
