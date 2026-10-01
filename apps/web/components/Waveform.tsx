"use client";

import { useEffect, useRef } from "react";

/**
 * 实时波形（五期，文档 3.9）：接 Web Audio AnalyserNode 画时域波形，
 * 未激活时画一条静止基线。录音聆听态与 TTS 播放态共用同一组件。
 * canvas 按 devicePixelRatio 缩放保证清晰，随容器宽度自适应。
 */
export function Waveform({
  analyser,
  active,
  color = "#014DB2",
  height = 56,
}: {
  analyser: AnalyserNode | null;
  active: boolean;
  color?: string;
  height?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number>(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.floor(rect.width * dpr));
      canvas.height = Math.max(1, Math.floor(height * dpr));
    };
    resize();
    window.addEventListener("resize", resize);

    const data = analyser ? new Uint8Array(analyser.frequencyBinCount) : null;

    const draw = () => {
      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);
      ctx.lineWidth = 2 * dpr;
      ctx.beginPath();
      if (analyser && active && data) {
        analyser.getByteTimeDomainData(data);
        const slice = w / data.length;
        let x = 0;
        for (let i = 0; i < data.length; i++) {
          const v = data[i] / 128.0; // 0~2，1 为中轴
          const y = (v * h) / 2;
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
          x += slice;
        }
        ctx.strokeStyle = color;
      } else {
        ctx.moveTo(0, h / 2);
        ctx.lineTo(w, h / 2);
        ctx.strokeStyle = "#EDEDED";
      }
      ctx.stroke();
      rafRef.current = requestAnimationFrame(draw);
    };
    draw();

    return () => {
      window.removeEventListener("resize", resize);
      cancelAnimationFrame(rafRef.current);
    };
  }, [analyser, active, color, height]);

  return <canvas ref={canvasRef} className="w-full" style={{ height }} />;
}
