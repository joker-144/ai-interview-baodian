"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { PageHeader, ProgressBar } from "@/components/ui";
import {
  getResumeAnalysis,
  startGenerate as startGenerateTask,
  streamGenerate,
  uploadAndParseResume,
} from "@/lib/api";
import type { GenerateProgress, ResumeAnalysis } from "@/lib/types";

type Stage = "upload" | "parsing" | "parsed" | "generating" | "done";

const STEPS = ["上传简历", "AI 解析", "生成题库"];

/** 后端兜底题量（正常情况下始终使用 analysis.estimatedCount） */
const FALLBACK_COUNT = 40;

export default function ResumePage() {
  const router = useRouter();
  const [stage, setStage] = useState<Stage>("upload");
  const [analysis, setAnalysis] = useState<ResumeAnalysis | null>(null);
  const [progress, setProgress] = useState<GenerateProgress | null>(null);
  const [error, setError] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [picked, setPicked] = useState<File | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getResumeAnalysis()
      .then((a) => {
        if (a) {
          setAnalysis(a);
          setStage("parsed");
        }
      })
      .catch(() => {
        // 首次进入尚无简历：静默保持上传态
      });
  }, []);

  const stepIndex = stage === "upload" ? 0 : stage === "parsing" ? 1 : stage === "parsed" ? 1 : 2;

  const handleFile = async (file: File) => {
    setPicked(file);
    setError("");
    setProgress(null);
    setStage("parsing");
    try {
      const result = await uploadAndParseResume(file);
      setAnalysis(result);
      setStage("parsed");
    } catch (err) {
      setError(err instanceof Error ? err.message : "简历解析失败，请重试");
      setStage(analysis ? "parsed" : "upload");
    }
  };

  const startGenerate = async () => {
    // 题量用 AI 解析预估出的真实值（40 / 80 / 120 三档），而非固定小批量
    const total = analysis?.estimatedCount ?? FALLBACK_COUNT;
    setStage("generating");
    setError("");
    setProgress({ generated: 0, total, currentDimension: "技能八股", done: false });
    try {
      const taskId = await startGenerateTask(total);
      let last: GenerateProgress | null = null;
      for await (const p of streamGenerate(taskId)) {
        last = p;
        setProgress(p);
      }
      if (last?.error) {
        setError(last.error);
        setStage("parsed");
        return;
      }
      setStage("done");
    } catch (err) {
      setError(err instanceof Error ? err.message : "出题失败，请重试");
      setStage("parsed");
    }
  };

  const doneCount = progress?.generated ?? 0;
  const totalCount = progress?.total ?? analysis?.estimatedCount ?? FALLBACK_COUNT;

  return (
    <div>
      <PageHeader title="简历 AI 分析" sub="上传简历，AI 深度解析你的经历与能力，生成专属面试题库" />

      {/* 流程步骤 */}
      <div className="mb-6 flex items-center gap-0">
        {STEPS.map((label, i) => (
          <div key={label} className="flex items-center">
            <div className="flex items-center gap-2">
              <span
                className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-medium ${
                  i < stepIndex
                    ? "bg-success text-white"
                    : i === stepIndex
                      ? "bg-brand text-white"
                      : "border border-line bg-card text-muted"
                }`}
              >
                {i < stepIndex ? (
                  <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
                    <path d="M3 8.5L6.5 12L13 4.5" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                ) : (
                  i + 1
                )}
              </span>
              <span className={`text-sm ${i === stepIndex ? "font-medium text-ink" : "text-muted"}`}>
                {label}
              </span>
            </div>
            {i < STEPS.length - 1 && <span className="mx-4 h-px w-16 bg-line" />}
          </div>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-4">
        {/* 上传区 */}
        <section className="card p-6">
          <h2 className="mb-4 text-base font-semibold">上传简历</h2>
          <div
            className={`flex flex-col items-center justify-center rounded-card border-2 border-dashed py-14 transition-colors ${
              dragOver ? "border-brand bg-brand-light/40" : "border-line"
            }`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              const f = e.dataTransfer.files?.[0];
              if (f) handleFile(f);
            }}
          >
            <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#9AA0AB" strokeWidth="1.5">
              <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M17 8l-5-5-5 5M12 3v12" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <button
              className="mt-4 text-sm font-medium text-brand"
              onClick={() => fileInput.current?.click()}
            >
              点击或拖拽上传简历
            </button>
            <p className="mt-1 text-xs text-muted">支持 PDF / Word / TXT · 最大 10MB · 需可复制文字的版本</p>
            <input
              ref={fileInput}
              type="file"
              accept=".pdf,.doc,.docx,.txt,.md"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) handleFile(f);
                e.target.value = ""; // 允许重复选同一个文件重新解析
              }}
            />
          </div>

          {stage === "parsing" && (
            <p className="mt-4 flex items-center gap-2 text-sm text-brand">
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-brand border-t-transparent" />
              AI 正在解析简历（真实调用主模型，约 10~30 秒）…
            </p>
          )}
          {analysis && stage !== "parsing" && (
            <div className="mt-4 flex items-center justify-between rounded-btn bg-bg px-4 py-3">
              <span className="flex items-center gap-2 text-sm">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#D95555" strokeWidth="1.8">
                  <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8l-6-6z" strokeLinejoin="round" />
                  <path d="M14 2v6h6" strokeLinejoin="round" />
                </svg>
                {analysis.fileName}
              </span>
              <span className="tag bg-green-50 text-success">状态：已解析</span>
            </div>
          )}
          {error && (
            <p className="mt-3 rounded-btn bg-red-50 px-3 py-2 text-xs text-danger">{error}</p>
          )}
        </section>

        {/* AI 解析结果 */}
        <section className="card p-6">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-base font-semibold">AI 解析结果</h2>
            {picked && (
              <button className="btn-secondary !px-3 !py-1 text-xs" onClick={() => handleFile(picked)}>
                重新解析
              </button>
            )}
          </div>

          {!analysis ? (
            <div className="flex h-52 flex-col items-center justify-center text-sm text-muted">
              <p>上传简历后，这里将展示解析结果</p>
              <p className="mt-2 text-xs">解析与出题均真实调用你配置的主模型，不再使用种子数据</p>
            </div>
          ) : (
            <>
              <div className="grid grid-cols-3 gap-3">
                <div className="rounded-btn bg-bg p-3 text-center">
                  <p className="text-xs text-muted">工作年限</p>
                  <p className="mt-1 text-lg font-semibold">
                    {analysis.years > 0 ? `${analysis.years} 年` : "应届"}
                  </p>
                </div>
                <div className="rounded-btn bg-bg p-3 text-center">
                  <p className="text-xs text-muted">目标岗位</p>
                  <p className="mt-1 text-base font-semibold">{analysis.targetRole}</p>
                </div>
                <div className="rounded-btn bg-bg p-3 text-center">
                  <p className="text-xs text-muted">预计生成题量</p>
                  <p className="mt-1 text-lg font-semibold text-brand">约 {analysis.estimatedCount} 题</p>
                </div>
              </div>

              <h3 className="mb-3 mt-5 text-sm font-semibold">能力维度评估</h3>
              <div className="space-y-3">
                {analysis.dimensions.map((d) => (
                  <div key={d.label} className="flex items-center gap-3">
                    <span className="w-16 text-sm text-ink">{d.label}</span>
                    <div className="flex-1">
                      <ProgressBar value={d.score} max={100} color={d.score >= 80 ? "bg-success" : d.score >= 70 ? "bg-brand" : "bg-warn"} />
                    </div>
                    <span className="w-8 text-right text-sm font-medium">{d.score}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </section>
      </div>

      {/* 底部注释 + 主按钮 */}
      <div className="card mt-4 p-6">
        {stage === "generating" || stage === "done" ? (
          <div>
            <div className="mb-2 flex items-center justify-between text-sm">
              <span className="font-medium">
                {stage === "done"
                  ? `专属题库已生成 ${doneCount} 题`
                  : `已生成 ${doneCount}/${totalCount} 题 · 当前维度：${progress?.currentDimension}`}
              </span>
              <span className="text-muted">{Math.round((doneCount / totalCount) * 100)}%</span>
            </div>
            <ProgressBar value={doneCount} max={totalCount} color={stage === "done" ? "bg-success" : "bg-brand"} />
            <p className="mt-2 text-xs text-muted">
              {stage === "done"
                ? `已生成的题立即可刷，题库中心已同步更新${progress?.dropped ? `（另有 ${progress.dropped} 题未通过校验/去重被淘汰）` : ""}`
                : "后台正在调用主模型出题，可随时离开，服务端会继续生成"}
            </p>
            {stage === "done" && (
              <button className="btn-primary mt-4" onClick={() => router.push("/bank")}>
                去题库中心刷题
              </button>
            )}
          </div>
        ) : (
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted">
              覆盖 L1 基础 → L3 深度 · 每题附参考回答 + 解析 · 按 AI 预估生成
              约 {analysis?.estimatedCount ?? FALLBACK_COUNT} 题（真实调用主模型，预计 3~10 分钟）
            </p>
            <button className="btn-primary" disabled={!analysis} onClick={startGenerate}>
              生成专属题库
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
