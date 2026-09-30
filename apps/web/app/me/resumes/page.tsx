"use client";

/**
 * 简历管理 · N 份（二期批 3，产品文档 8.3 /me/resumes）
 *
 * - 多版本列表（version 降序）：各版本挂体检报告入口、DOCX 下载、删除；
 * - 上传/重新解析统一走 /resume（本页不做上传，保持单一入口）；
 * - 出题默认用最新一份（后端 /latest 语义），删除最新版时后端自动回退到次新版。
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { PageHeader } from "@/components/ui";
import { deleteResume, downloadResumeDocx, getResumes } from "@/lib/api";
import type { ResumeVersion } from "@/lib/types";

export default function MyResumesPage() {
  const [versions, setVersions] = useState<ResumeVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = () =>
    getResumes()
      .then(setVersions)
      .catch((err) => setError(err instanceof Error ? err.message : "加载简历列表失败"))
      .finally(() => setLoading(false));

  useEffect(() => {
    load();
  }, []);

  const handleDownload = async (v: ResumeVersion) => {
    setBusyId(v.resumeId);
    setError("");
    try {
      const base = (v.analysis?.fileName || v.fileName || "简历").replace(/\.docx$/i, "");
      await downloadResumeDocx(v.resumeId, base);
    } catch (err) {
      setError(err instanceof Error ? err.message : "下载失败，请重试");
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (v: ResumeVersion) => {
    if (!window.confirm(`确定删除「${v.analysis?.fileName ?? v.fileName}」v${v.version}？删除后不可恢复`)) {
      return;
    }
    setBusyId(v.resumeId);
    setError("");
    try {
      await deleteResume(v.resumeId);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败，请重试");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div>
      <PageHeader
        title="简历管理"
        sub={loading ? "加载中…" : `共 ${versions.length} 份 · 各版本体检报告与 DOCX 下载`}
      />

      <div className="card p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold">全部版本</h2>
          <Link href="/resume" className="btn-primary !px-4 !py-1.5 text-sm">
            上传 / 重新解析
          </Link>
        </div>

        {error && (
          <p className="mb-3 rounded-btn bg-red-50 px-3 py-2 text-xs text-danger">{error}</p>
        )}

        {loading ? (
          <div className="flex h-32 items-center justify-center text-sm text-muted">加载中…</div>
        ) : versions.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
            <p className="text-sm text-ink">还没有简历</p>
            <p className="text-xs text-muted">上传简历后可在此管理多版本，并查看各版本体检报告</p>
            <Link href="/resume" className="btn-primary mt-2">去上传简历</Link>
          </div>
        ) : (
          <ul className="space-y-3">
            {versions.map((v) => (
              <li
                key={v.resumeId}
                className="flex flex-wrap items-center gap-3 rounded-btn border border-line px-4 py-3"
              >
                <div className="min-w-0 flex-1">
                  <p className="flex items-center gap-2 text-sm text-ink">
                    <span className="truncate">{v.analysis?.fileName ?? v.fileName}</span>
                    <span
                      className={`tag shrink-0 ${
                        v.isOptimized ? "bg-success/10 text-success" : "bg-line/70 text-muted"
                      }`}
                    >
                      {v.isOptimized ? "优化版" : "原版"}
                    </span>
                  </p>
                  <p className="mt-0.5 text-xs text-muted">
                    v{v.version} · {v.createdAt.slice(0, 16)} · 目标岗位：
                    {v.analysis?.targetRole ?? "—"}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Link
                    href={`/resume/report?v=${v.resumeId}`}
                    className="btn-secondary !px-3 !py-1.5 text-xs"
                  >
                    体检报告
                  </Link>
                  <button
                    className="btn-secondary !px-3 !py-1.5 text-xs"
                    onClick={() => handleDownload(v)}
                    disabled={busyId === v.resumeId}
                  >
                    {busyId === v.resumeId ? "处理中…" : "下载"}
                  </button>
                  <button
                    className="text-xs text-muted underline-offset-2 transition-colors hover:text-danger hover:underline"
                    onClick={() => handleDelete(v)}
                    disabled={busyId === v.resumeId}
                  >
                    删除
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <p className="mt-3 text-xs leading-relaxed text-muted">
        出题默认使用最新一份简历的解析画像；体检报告由 AI 一次解析同时产出，
        旧版本首次查看时自动补算评分。
      </p>
    </div>
  );
}
