"use client";

/**
 * P6 JD 精准定制（六期引擎 C，文档 3.6 / 原型 P5）。
 *
 * 三通道输入（粘贴链接 / 上传截图 / 粘贴文本）→ 统一结构化 JD 解析面板
 * → 岗位匹配度报告（强匹配 / 弱匹配 / 缺口三分类，弱项与缺口可勾选生成补强题包）
 * → 定向出题（「结合我的简历生成」开关 + 题量/难度/附答案 chips + SSE 进度）。
 *
 * 看板手动卡 / 外部平台卡经 `?cardId=` 进入（默认切到粘贴文本兜底通道），
 * 生成的题集自动挂接该卡；截图识别失败可一键切换「粘贴文本」降级。
 */
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import { PageHeader, ProgressBar } from "@/components/ui";
import {
  getJdMatchReport,
  getResumes,
  isLoggedIn,
  parseJd,
  startJdCustomGenerate,
  streamGenerate,
} from "@/lib/api";
import type {
  GenerateProgress,
  JdInputChannel,
  JdMatchReport,
  JdParseResult,
} from "@/lib/types";

const CHANNELS: { key: JdInputChannel; label: string; hint: string }[] = [
  {
    key: "link",
    label: "粘贴链接",
    hint: "BOSS 直聘岗位详情页链接，自动解析并关联看板卡（推荐）",
  },
  {
    key: "screenshot",
    label: "上传截图",
    hint: "JD 截图 AI 视觉提取，支持智联 / 前程无忧 / 猎聘等任意平台",
  },
  {
    key: "text",
    label: "粘贴文本",
    hint: "粘贴 JD 原文，终极兜底通道，任何平台都可用",
  },
];

const COUNT_OPTIONS = [10, 30, 50];
const DIFFICULTY_OPTIONS = ["混合", "L1", "L2", "L3"];
/** 补强突击题包固定题量：聚焦缺口主题的短平快训练 */
const REMEDIAL_COUNT = 6;

function scoreTone(score: number): { cls: string; label: string } {
  if (score >= 75) return { cls: "bg-success/10 text-success", label: "高度匹配" };
  if (score >= 50) return { cls: "bg-warn/10 text-warn", label: "基本匹配" };
  return { cls: "bg-danger/10 text-danger", label: "匹配偏低" };
}

export default function JdPage() {
  return (
    <Suspense fallback={<div className="py-24 text-center text-sm text-muted">加载中…</div>}>
      <JdPageInner />
    </Suspense>
  );
}

function JdPageInner() {
  const searchParams = useSearchParams();
  // 看板手动卡 / 外部平台卡入口：生成的题集自动挂接到该卡
  const cardId = (searchParams.get("cardId") || "").trim();

  const [authed, setAuthed] = useState<boolean | null>(null);
  const [hasResume, setHasResume] = useState(false);

  // 三通道输入
  const [channel, setChannel] = useState<JdInputChannel>(cardId ? "text" : "link");
  const [url, setUrl] = useState("");
  const [image, setImage] = useState<{ dataUri: string; name: string } | null>(null);
  const [text, setText] = useState("");

  // 解析
  const [parsing, setParsing] = useState(false);
  const [parseError, setParseError] = useState("");
  const [parsed, setParsed] = useState<JdParseResult | null>(null);

  // 匹配度报告（解析成功且有简历时自动评估）
  const [report, setReport] = useState<JdMatchReport | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportHint, setReportHint] = useState("");

  // 补强题包：弱项 + 缺口条目勾选（默认全选）
  const [remedialPicked, setRemedialPicked] = useState<Set<string>>(new Set());

  // 出题设置（原型 P4 chips 同款交互）
  const [useResume, setUseResume] = useState(true);
  const [count, setCount] = useState(30);
  const [difficulty, setDifficulty] = useState("混合");
  const [withAnswer, setWithAnswer] = useState(true);

  // 出题进度（复用引擎 A 的 SSE 协议）
  const [generating, setGenerating] = useState(false);
  const [progress, setProgress] = useState<GenerateProgress | null>(null);
  const [genError, setGenError] = useState("");
  const [finishedSetId, setFinishedSetId] = useState("");
  /** 本次生成产物标签：专属题库 / 补强题包（完成提示区分） */
  const [genLabel, setGenLabel] = useState("专属题库");

  useEffect(() => {
    if (!isLoggedIn()) {
      setAuthed(false);
      return;
    }
    setAuthed(true);
    getResumes()
      .then((list) => setHasResume(list.length > 0))
      .catch(() => setHasResume(false));
  }, []);

  const switchChannel = (key: JdInputChannel) => {
    setChannel(key);
    setParseError("");
  };

  const onFile = (file: File | null) => {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setParseError("请上传图片文件（PNG / JPG / WebP）");
      return;
    }
    if (file.size > 9 * 1024 * 1024) {
      setParseError("截图过大（约超过 9MB），请裁剪或压缩后重试");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setImage({ dataUri: String(reader.result), name: file.name });
      setParseError("");
    };
    reader.onerror = () => setParseError("图片读取失败，请重试");
    reader.readAsDataURL(file);
  };

  const handleParse = async () => {
    setParseError("");
    if (channel === "link" && !url.trim()) {
      setParseError("请粘贴 Boss 直聘岗位页链接");
      return;
    }
    if (channel === "screenshot" && !image) {
      setParseError("请先上传 JD 截图");
      return;
    }
    if (channel === "text" && text.trim().length < 50) {
      setParseError("JD 原文太短（至少 50 字）：请粘贴完整岗位职责与要求");
      return;
    }
    setParsing(true);
    try {
      const res = await parseJd({
        channel,
        url: channel === "link" ? url.trim() : undefined,
        imageBase64: channel === "screenshot" ? image?.dataUri : undefined,
        text: channel === "text" ? text : undefined,
      });
      setParsed(res);
      setReport(null);
      setReportHint("");
      setFinishedSetId("");
      setProgress(null);
      setGenError("");
      // 有简历则自动评估匹配度（无简历不报错，出题区给出上传引导）
      if (hasResume) {
        setReportLoading(true);
        try {
          setReport(await getJdMatchReport(res.jd));
        } catch (exc) {
          setReportHint(exc instanceof Error ? exc.message : "匹配度评估失败，可稍后在出题时重试");
        } finally {
          setReportLoading(false);
        }
      }
    } catch (exc) {
      setParseError(exc instanceof Error ? exc.message : "解析失败，请稍后重试");
      setParsed(null);
    } finally {
      setParsing(false);
    }
  };

  /** 发起出题并订阅 SSE 进度（专属题库 / 补强题包共用） */
  const runGenerate = async (
    params: Parameters<typeof startJdCustomGenerate>[0],
    label: string,
  ) => {
    setGenError("");
    setFinishedSetId("");
    setProgress(null);
    setGenLabel(label);
    setGenerating(true);
    try {
      const taskId = await startJdCustomGenerate(params);
      setProgress({
        generated: 0,
        total: params.count,
        currentDimension: "解析岗位 JD",
        done: false,
        dropped: 0,
        setId: null,
      });
      for await (const p of streamGenerate(taskId)) {
        setProgress(p);
      }
      setProgress((prev) => {
        if (prev?.error) setGenError(prev.error);
        else if (prev?.setId) setFinishedSetId(prev.setId);
        return prev;
      });
    } catch (exc) {
      setGenError(exc instanceof Error ? exc.message : "生成任务发起失败，请稍后重试");
      setProgress(null);
    } finally {
      setGenerating(false);
    }
  };

  const handleGenerate = () => {
    if (!parsed || generating) return;
    void runGenerate(
      {
        inputChannel: parsed.jd.channel,
        securityId: parsed.jd.securityId,
        jd: parsed.jd,
        jobName: parsed.jd.jobName,
        useResume: useResume && hasResume,
        count,
        difficulty,
        withAnswer,
        cardId: cardId || undefined,
      },
      "专属题库",
    );
  };

  const handleRemedial = () => {
    if (!parsed || generating || remedialPicked.size === 0) return;
    void runGenerate(
      {
        inputChannel: parsed.jd.channel,
        securityId: parsed.jd.securityId,
        jd: parsed.jd,
        jobName: parsed.jd.jobName,
        useResume: false, // 补强题包专攻缺口主题，不注入简历上下文
        count: REMEDIAL_COUNT,
        difficulty: "混合",
        withAnswer: true,
        remedialTopics: Array.from(remedialPicked),
        // 不传 cardId：手动卡挂接保持主题集，补强包作为独立练习材料
      },
      "补强题包",
    );
  };

  const toggleTopic = (topic: string) => {
    setRemedialPicked((prev) => {
      const next = new Set(prev);
      if (next.has(topic)) next.delete(topic);
      else next.add(topic);
      return next;
    });
  };

  if (authed === null) {
    return <div className="py-24 text-center text-sm text-muted">加载中…</div>;
  }
  if (!authed) {
    return (
      <div className="card mx-auto mt-16 max-w-md p-8 text-center">
        <h1 className="text-lg font-semibold">登录后开始 JD 精准定制</h1>
        <p className="mt-2 text-sm text-muted">
          三通道解析目标 JD，结合简历生成定向题库与岗位匹配度报告。
        </p>
        <Link href="/login" className="btn-primary mt-5 inline-flex">
          去登录
        </Link>
      </div>
    );
  }

  const jd = parsed?.jd;
  const score = report?.matchScore ?? null;
  const percent =
    progress && progress.total > 0
      ? Math.min(100, Math.round((progress.generated / progress.total) * 100))
      : 0;

  return (
    <main className="mx-auto max-w-3xl px-4 py-6">
      <PageHeader
        title="JD 精准定制"
        sub="粘贴链接 / 上传截图 / 粘贴文本三通道解析目标 JD，结合简历生成定向题库、岗位匹配度报告与缺口补强题包。"
      />

      {cardId && (
        <section className="mb-4 rounded-xl border border-brand/30 bg-brand/5 p-3 text-sm">
          正在看板卡模式下出题：生成的<span className="font-medium text-brand">专属题库将自动挂接该看板卡</span>
          ，练习进度直接在看板跟进。
        </section>
      )}

      {/* ---------- 三通道输入 ---------- */}
      <section className="card p-4">
        <div className="flex flex-wrap gap-1.5">
          {CHANNELS.map((c) => (
            <button
              key={c.key}
              type="button"
              onClick={() => switchChannel(c.key)}
              className={`rounded-full px-3 py-1 text-xs font-medium ${
                channel === c.key
                  ? "bg-brand text-white"
                  : "border border-line text-muted hover:border-brand hover:text-brand"
              }`}
            >
              {c.label}
            </button>
          ))}
        </div>
        <p className="mt-2 text-xs text-muted">{CHANNELS.find((c) => c.key === channel)?.hint}</p>

        <div className="mt-3">
          {channel === "link" && (
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://www.zhipin.com/job_detail/xxx.html?securityId=…"
              className="input"
            />
          )}
          {channel === "screenshot" && (
            <div>
              <label className="flex cursor-pointer items-center justify-center rounded-xl border border-dashed border-line py-6 text-sm text-muted hover:border-brand hover:text-brand">
                {image ? `已选择：${image.name}（点击更换）` : "点击选择 JD 截图（PNG / JPG / WebP，≤9MB）"}
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  className="hidden"
                  onChange={(e) => onFile(e.target.files?.[0] ?? null)}
                />
              </label>
              {image && (
                <div className="mt-2 flex items-center gap-2">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={image.dataUri}
                    alt="JD 截图预览"
                    className="h-20 rounded-lg border border-line object-cover"
                  />
                  <button
                    type="button"
                    onClick={() => setImage(null)}
                    className="text-xs text-muted hover:text-danger"
                  >
                    移除
                  </button>
                </div>
              )}
            </div>
          )}
          {channel === "text" && (
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={7}
              placeholder="粘贴岗位职责与任职要求全文（至少 50 字），支持任意招聘平台格式"
              className="input resize-y"
            />
          )}
        </div>

        <div className="mt-3 flex items-center gap-3">
          <button
            type="button"
            onClick={handleParse}
            disabled={parsing}
            className="btn-primary disabled:opacity-50"
          >
            {parsing ? "解析中…" : "解析 JD"}
          </button>
          {parseError && (
            <div className="min-w-0 flex-1">
              <p className="text-xs text-danger">{parseError}</p>
              {/* 截图识别失败 → 引导降级到粘贴文本（文档验收标准） */}
              {channel === "screenshot" && /识别|截图/.test(parseError) && (
                <button
                  type="button"
                  onClick={() => switchChannel("text")}
                  className="mt-1 text-xs font-medium text-brand hover:underline"
                >
                  改用「粘贴文本」通道 →
                </button>
              )}
            </div>
          )}
        </div>
      </section>

      {/* ---------- 解析结果面板 ---------- */}
      {parsed && jd && (
        <section className="card mt-4 p-4">
          <div className="flex items-start justify-between gap-2">
            <h2 className="min-w-0 text-base font-semibold">{jd.jobName}</h2>
            {score !== null && (
              <span
                className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-semibold ${scoreTone(score).cls}`}
                title={scoreTone(score).label}
              >
                匹配度 {score}%
              </span>
            )}
            {reportLoading && <span className="shrink-0 text-xs text-muted">评估匹配度中…</span>}
          </div>
          <p className="mt-1 text-sm text-muted">
            {[jd.brand, jd.city, jd.salary, jd.experience].filter(Boolean).join(" · ") || "—"}
          </p>
          {parsed.retrievalMatched === false && jd.channel === "screenshot" && (
            <p className="mt-1.5 text-xs text-warn">
              未能从岗位库反查锁定该岗位，已按截图视觉提取结果解析（降级模式），出题素材可能略有出入。
            </p>
          )}
          {jd.skills.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {jd.skills.map((s) => (
                <span key={s} className="tag bg-line/60 text-ink">
                  {s}
                </span>
              ))}
            </div>
          )}
          {jd.desc && <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-ink/80">{jd.desc}</p>}
        </section>
      )}

      {/* ---------- 匹配度报告 ---------- */}
      {parsed && (
        <section className="card mt-4 p-4">
          <h2 className="text-base font-semibold">岗位匹配度报告</h2>
          {!hasResume ? (
            <div className="mt-2 text-sm text-muted">
              上传简历后即可评估该岗位与你的匹配度，并在出题时生成完整报告（强匹配 / 弱匹配 / 缺口）。
              <Link href="/resume" className="ml-1 font-medium text-brand hover:underline">
                去上传简历 →
              </Link>
            </div>
          ) : reportLoading ? (
            <p className="mt-2 text-sm text-muted">正在结合你的简历评估匹配度…</p>
          ) : report ? (
            <div className="mt-2">
              <p className="text-sm leading-relaxed text-ink/80">{report.summary}</p>
              <div className="mt-3 grid gap-3 sm:grid-cols-3">
                <div className="rounded-xl bg-success/5 p-3">
                  <p className="text-xs font-semibold text-success">✓ 强匹配（{report.strong.length}）</p>
                  <ul className="mt-1.5 space-y-1.5">
                    {report.strong.map((it) => (
                      <li key={it.item} className="text-xs text-ink/80">
                        {it.item}
                        {it.evidence && <span className="block text-muted">{it.evidence}</span>}
                      </li>
                    ))}
                    {report.strong.length === 0 && <li className="text-xs text-muted">暂无</li>}
                  </ul>
                </div>
                <div className="rounded-xl bg-warn/5 p-3">
                  <p className="text-xs font-semibold text-warn">△ 弱匹配（{report.weak.length}）</p>
                  <ul className="mt-1.5 space-y-1.5">
                    {report.weak.map((it) => (
                      <li key={it.item}>
                        <label className="flex cursor-pointer items-start gap-1.5 text-xs text-ink/80">
                          <input
                            type="checkbox"
                            checked={remedialPicked.has(it.item)}
                            onChange={() => toggleTopic(it.item)}
                            className="mt-0.5"
                          />
                          <span>
                            {it.item}
                            {it.advice && <span className="block text-muted">{it.advice}</span>}
                          </span>
                        </label>
                      </li>
                    ))}
                    {report.weak.length === 0 && <li className="text-xs text-muted">暂无</li>}
                  </ul>
                </div>
                <div className="rounded-xl bg-danger/5 p-3">
                  <p className="text-xs font-semibold text-danger">✕ 能力缺口（{report.gap.length}）</p>
                  <ul className="mt-1.5 space-y-1.5">
                    {report.gap.map((it) => (
                      <li key={it.item}>
                        <label className="flex cursor-pointer items-start gap-1.5 text-xs text-ink/80">
                          <input
                            type="checkbox"
                            checked={remedialPicked.has(it.item)}
                            onChange={() => toggleTopic(it.item)}
                            className="mt-0.5"
                          />
                          <span>
                            {it.item}
                            {it.advice && <span className="block text-muted">{it.advice}</span>}
                          </span>
                        </label>
                      </li>
                    ))}
                    {report.gap.length === 0 && <li className="text-xs text-muted">暂无</li>}
                  </ul>
                </div>
              </div>
              {remedialPicked.size > 0 && (
                <button
                  type="button"
                  onClick={handleRemedial}
                  disabled={generating}
                  className="mt-3 rounded-lg border border-brand px-3 py-1.5 text-sm font-medium text-brand hover:bg-brand-light disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {generating && genLabel === "补强题包"
                    ? "生成中…"
                    : `生成补强题包（${remedialPicked.size} 个主题 · ${REMEDIAL_COUNT} 题）`}
                </button>
              )}
            </div>
          ) : (
            <p className="mt-2 text-sm text-muted">
              {reportHint || "解析成功后自动评估；本次评估未完成，可在生成题库时产出完整报告。"}
            </p>
          )}
        </section>
      )}

      {/* ---------- 定向出题 ---------- */}
      {parsed && jd && (
        <section className="card mt-4 p-4">
          <h2 className="text-base font-semibold">生成定向题库</h2>
          <div className="mt-3 space-y-3 text-sm">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="font-medium">结合我的简历生成</p>
                <p className="text-xs text-muted">
                  {hasResume
                    ? "注入简历背景出题（追问项目经历与真实水平），并随题库产出完整匹配度报告"
                    : "尚未上传简历：上传后可结合简历背景出题并生成匹配度报告"}
                </p>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={useResume && hasResume}
                disabled={!hasResume || generating}
                onClick={() => setUseResume((v) => !v)}
                className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${
                  useResume && hasResume ? "bg-brand" : "bg-line"
                } disabled:cursor-not-allowed disabled:opacity-50`}
              >
                <span
                  className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${
                    useResume && hasResume ? "left-[22px]" : "left-0.5"
                  }`}
                />
              </button>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted">题量</span>
              {COUNT_OPTIONS.map((n) => (
                <button
                  key={n}
                  type="button"
                  disabled={generating}
                  onClick={() => setCount(n)}
                  className={`rounded-full px-3 py-1 text-xs ${
                    count === n
                      ? "bg-brand text-white"
                      : "border border-line text-muted hover:border-brand hover:text-brand"
                  } disabled:opacity-40`}
                >
                  {n}
                </button>
              ))}
              <span className="ml-2 text-xs text-muted">难度</span>
              {DIFFICULTY_OPTIONS.map((d) => (
                <button
                  key={d}
                  type="button"
                  disabled={generating}
                  onClick={() => setDifficulty(d)}
                  className={`rounded-full px-3 py-1 text-xs ${
                    difficulty === d
                      ? "bg-brand text-white"
                      : "border border-line text-muted hover:border-brand hover:text-brand"
                  } disabled:opacity-40`}
                >
                  {d}
                </button>
              ))}
              <button
                type="button"
                disabled={generating}
                onClick={() => setWithAnswer((v) => !v)}
                className={`ml-2 rounded-full px-3 py-1 text-xs ${
                  withAnswer
                    ? "bg-brand text-white"
                    : "border border-line text-muted hover:border-brand hover:text-brand"
                } disabled:opacity-40`}
              >
                附参考答案+解析
              </button>
            </div>

            <button
              type="button"
              onClick={handleGenerate}
              disabled={generating}
              className="btn-primary disabled:cursor-not-allowed disabled:opacity-50"
            >
              {generating && genLabel === "专属题库"
                ? "生成中…"
                : `生成「${jd.jobName}」专属预测题`}
            </button>
          </div>

          {/* SSE 进度（与引擎 A 出题页同款） */}
          {progress && !genError && !finishedSetId && (
            <div className="mt-4">
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted">
                  正在生成{genLabel === "补强题包" ? "补强题包" : "专属预测题"}… {progress.generated}/
                  {progress.total}
                  {progress.dropped ? `（已淘汰 ${progress.dropped}）` : ""}
                </span>
                <span className="font-medium text-brand">{percent}%</span>
              </div>
              <div className="mt-1">
                <ProgressBar value={progress.generated} max={progress.total} />
              </div>
              <p className="mt-1 truncate text-xs text-muted/80" title={progress.currentDimension}>
                当前维度：{progress.currentDimension}
              </p>
            </div>
          )}
          {genError && <p className="mt-3 text-sm text-danger">{genError}</p>}
          {finishedSetId && (
            <div className="mt-4 rounded-xl border border-success/40 bg-success/5 p-3 text-sm">
              <p className="font-medium text-success">
                {genLabel}已生成{cardId && genLabel === "专属题库" ? "，并已挂接到看板卡片" : ""}
              </p>
              <Link href={`/practice/${finishedSetId}`} className="mt-1 inline-block font-medium text-brand hover:underline">
                去刷题 →
              </Link>
            </div>
          )}
        </section>
      )}

      <p className="mt-4 text-center text-xs text-muted/70">
        仅解析你主动输入的 JD · 链接通道仅支持 BOSS 直聘只读详情 · 截图 / 文本通道完全离线
      </p>
    </main>
  );
}
