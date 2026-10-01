"use client";

/**
 * 面试复盘报告（五期，文档 3.9）：/interview/report/[id]
 *
 * 综合得分环 + STAR 四维雷达 + 总评/亮点/改进建议 + 每题文字稿回放
 * （含一层追问轮次；开启录音留存时可回放音频）。数据来自 primary 层复盘。
 */

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { PageHeader } from "@/components/ui";
import { fetchInterviewAudio, getInterviewReport } from "@/lib/api";
import {
  INTERVIEW_MODE_LABEL,
  STAR_LABEL,
  type InterviewReportPayload,
  type StarScores,
} from "@/lib/types";

const KEYS: (keyof StarScores)[] = ["situation", "task", "action", "result"];
const DIM_TAG: Record<string, string> = {
  STAR: "bg-purple-50 text-ai",
  技术: "bg-brand-light text-brand",
  HR: "bg-orange-50 text-warn",
};

/** 综合得分环（0~100） */
function ScoreRing({ score }: { score: number }) {
  const r = 54;
  const c = 2 * Math.PI * r;
  const filled = (Math.min(Math.max(score, 0), 100) / 100) * c;
  const tone = score >= 80 ? "#1E9E6A" : score >= 60 ? "#3B82F6" : "#E5484D";
  return (
    <div className="relative flex h-36 w-36 items-center justify-center">
      <svg width="144" height="144" viewBox="0 0 144 144" className="-rotate-90">
        <circle cx="72" cy="72" r={r} fill="none" stroke="#EDEDED" strokeWidth="12" />
        <circle cx="72" cy="72" r={r} fill="none" stroke={tone} strokeWidth="12" strokeLinecap="round"
          strokeDasharray={`${filled} ${c - filled}`} />
      </svg>
      <div className="absolute text-center">
        <span className="text-4xl font-semibold" style={{ color: tone }}>{score}</span>
        <span className="block text-xs text-muted">综合得分</span>
      </div>
    </div>
  );
}

/** STAR 四维雷达图（各维 0~10） */
function Radar({ scores }: { scores: StarScores }) {
  const cx = 100, cy = 96, R = 62;
  const angle = (i: number) => (Math.PI / 2) * i - Math.PI / 2; // 顶→右→底→左
  const pt = (i: number, ratio: number): [number, number] => {
    const a = angle(i);
    return [cx + Math.cos(a) * R * ratio, cy + Math.sin(a) * R * ratio];
  };
  const rings = [0.25, 0.5, 0.75, 1];
  const dataPoly = KEYS.map((k, i) => pt(i, Math.max(0.05, (scores[k] ?? 0) / 10)).join(",")).join(" ");
  const labels = KEYS.map((k, i) => {
    const [x, y] = pt(i, 1.24);
    return { k, x, y, label: STAR_LABEL[k], v: scores[k] ?? 0 };
  });
  return (
    <svg width="200" height="192" viewBox="0 0 200 192">
      {rings.map((rr) => (
        <polygon
          key={rr}
          points={KEYS.map((_, i) => pt(i, rr).join(",")).join(" ")}
          fill="none" stroke="#EDEDED" strokeWidth="1"
        />
      ))}
      {KEYS.map((_, i) => {
        const [x, y] = pt(i, 1);
        return <line key={i} x1={cx} y1={cy} x2={x} y2={y} stroke="#EDEDED" strokeWidth="1" />;
      })}
      <polygon points={dataPoly} fill="rgba(108,92,231,0.18)" stroke="#6C5CE7" strokeWidth="2" />
      {labels.map((l) => (
        <text key={l.k} x={l.x} y={l.y} textAnchor="middle" dominantBaseline="middle"
          className="fill-muted" style={{ fontSize: 12 }}>
          {l.label} {l.v}
        </text>
      ))}
    </svg>
  );
}

/** 录音回放（仅开启留存时后端返回；需 JWT 拉 Blob） */
function ReplayAudio({ sessionId, answerId }: { sessionId: string; answerId: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]);
  const load = async () => {
    setLoading(true);
    setErr("");
    try {
      const blob = await fetchInterviewAudio(sessionId, answerId);
      setUrl(URL.createObjectURL(blob));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  };
  if (url) return <audio controls src={url} className="mt-2 h-9 w-full" />;
  return (
    <button className="mt-1 text-xs text-brand hover:underline" onClick={load} disabled={loading}>
      {loading ? "加载中…" : "▶ 回放录音"}
      {err && <span className="text-danger">（{err}）</span>}
    </button>
  );
}

export default function InterviewReportPage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [data, setData] = useState<InterviewReportPayload | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getInterviewReport(id)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "加载复盘失败"));
  }, [id]);

  if (error && !data) {
    return <div className="card border-warn/30 bg-warn/5 mx-auto mt-10 max-w-xl px-4 py-3 text-sm text-warn">{error}</div>;
  }
  if (!data || !data.report) {
    return <div className="py-24 text-center text-sm text-muted">正在生成复盘…</div>;
  }

  const report = data.report;

  return (
    <div>
      <PageHeader
        title="面试复盘报告"
        sub={`${INTERVIEW_MODE_LABEL[data.mode]}${data.targetJob ? ` · ${data.targetJob}` : ""} · 完成于 ${data.finishedAt ?? "—"} · 作答 ${data.answered}/${data.total} 题`}
      />

      {/* 概览：得分环 + 雷达 + 总评 */}
      <div className="grid grid-cols-3 gap-4">
        <section className="card col-span-1 flex flex-col items-center p-6">
          <ScoreRing score={report.overall} />
          <Radar scores={report.radar} />
        </section>
        <section className="card col-span-2 p-6">
          <h2 className="mb-2 text-base font-semibold">面试官总评</h2>
          <p className="text-sm leading-7 text-ink">{report.summary}</p>
          <div className="mt-5 grid grid-cols-2 gap-4">
            <div>
              <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-success">
                <span className="h-1.5 w-1.5 rounded-full bg-success" />亮点
              </h3>
              {report.strengths.length === 0 ? (
                <p className="text-xs text-muted">暂无</p>
              ) : (
                <ul className="space-y-1.5">
                  {report.strengths.map((s, i) => (
                    <li key={i} className="flex gap-2 text-sm text-ink"><span className="text-success">·</span>{s}</li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-warn">
                <span className="h-1.5 w-1.5 rounded-full bg-warn" />改进建议
              </h3>
              {report.improvements.length === 0 ? (
                <p className="text-xs text-muted">暂无</p>
              ) : (
                <ul className="space-y-1.5">
                  {report.improvements.map((s, i) => (
                    <li key={i} className="flex gap-2 text-sm text-ink"><span className="text-warn">{i + 1}.</span>{s}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </section>
      </div>

      {/* 逐题回放 */}
      <h2 className="mb-3 mt-6 text-base font-semibold">逐题回放</h2>
      <div className="space-y-4">
        {data.answers.map((a, idx) => (
          <section key={a.id} className="card p-5">
            <div className="mb-2 flex items-center gap-2">
              <span className="text-xs text-muted">第 {idx + 1} 题</span>
              <span className={`tag ${DIM_TAG[a.question?.dimension] ?? "bg-bg text-muted"}`}>{a.question?.dimension}</span>
              {a.starScores && (
                <span className="ml-auto flex items-center gap-2 text-xs text-muted">
                  {KEYS.map((k) => (
                    <span key={k}>{STAR_LABEL[k]} <strong className="text-ink">{a.starScores?.[k] ?? 0}</strong></span>
                  ))}
                </span>
              )}
            </div>
            <h3 className="text-sm font-medium leading-6">{a.question?.stem}</h3>

            <div className="mt-3 rounded-btn bg-bg px-4 py-3">
              <p className="text-xs text-muted">你的回答</p>
              <p className="mt-1 whitespace-pre-wrap text-sm leading-6">{a.transcript || "（未作答）"}</p>
              {a.hasRecording && <ReplayAudio sessionId={data.id} answerId={a.id} />}
            </div>

            {a.comment && <p className="mt-3 text-sm text-muted">点评：{a.comment}</p>}

            {(a.hitKeywords.length > 0 || a.missedKeywords.length > 0) && (
              <div className="mt-3 flex flex-wrap gap-2">
                {a.hitKeywords.map((k) => <span key={`h-${k}`} className="tag bg-green-50 text-success">✓ {k}</span>)}
                {a.missedKeywords.map((k) => <span key={`m-${k}`} className="tag bg-bg text-muted">{k}</span>)}
              </div>
            )}

            {a.turns && a.turns.length > 0 && (
              <div className="mt-3 space-y-2 border-t border-line pt-3">
                {a.turns.map((t) => (
                  <div key={t.turnNo} className="text-sm">
                    <span className={`tag mr-2 ${t.role === "interviewer" ? "bg-purple-50 text-ai" : "bg-brand-light text-brand"}`}>
                      {t.role === "interviewer" ? "面试官追问" : "你的回答"}
                    </span>
                    <span className="whitespace-pre-wrap text-ink">{t.transcript || "（未作答）"}</span>
                  </div>
                ))}
              </div>
            )}
          </section>
        ))}
        {data.answers.length === 0 && (
          <div className="card py-10 text-center text-sm text-muted">本场没有已作答的题目</div>
        )}
      </div>

      <div className="mt-6 flex gap-3">
        <button className="btn-primary" onClick={() => router.push("/interview")}>再来一场</button>
        <button className="btn-secondary" onClick={() => router.push("/interview")}>返回模拟面试</button>
      </div>
    </div>
  );
}
