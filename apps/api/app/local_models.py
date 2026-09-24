"""本地模型运行时：权重放在项目内 `apps/api/models/`，加载即推理，不走公网 API。

为什么本地跑向量模型（文档 4.6.1 第 2 条 / design-overview 第 5 节）：
- Embedding 是**高频、纯计算、无生成质量差异**的调用：题目去重与知识点检索每次出题
  要打几百次向量，云端按 token 计费且受网络抖动影响，本地跑边际成本为 0；
- 简历/JD 属敏感数据，向量化留在本机可减少一处出网环节；
- 生成式模型（主模型/轻量模型/多模态）仍走云端供应商，本地不承接。

约定：
- 权重由 `scripts/download_models.py` 下载，**入仓跟踪**（团队共享、免重复下载）；
- 下载脚本写出 `models/manifest.json`，本模块与管理端「本地模型」列表都以它为准；
- 懒加载 + 进程内单例：不调用就不占内存，首次加载约 3~8s（CPU）；
- 依赖缺失（未装 sentence-transformers / 权重未下载）时抛明确错误信息，
  由管理端连通性自测回显，绝不让服务启动失败。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from app.config import settings

WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")

# 线程限流必须在 numpy / torch 载入 OpenBLAS 之前设置（即在本模块 import 时），
# 否则受限环境（容器 / 沙箱 / 低内存）下会报
# "OpenBLAS error: Memory allocation still failed after N retries"。
# 用 setdefault：保留部署方显式指定的值。
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_var, str(settings.local_model_threads))

# 进程内模型单例：{model_id: (加载耗时秒, SentenceTransformer 实例)}
_LOADED: dict[str, tuple[float, object]] = {}


def models_dir() -> Path:
    return Path(settings.local_models_dir).resolve()


def manifest_path() -> Path:
    return models_dir() / "manifest.json"


def read_manifest() -> dict:
    """读取 manifest.json；缺失或损坏时返回空清单（不抛异常，管理端仍可展示引导）。"""
    path = manifest_path()
    if not path.exists():
        return {"updatedAt": "", "models": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"updatedAt": "", "models": []}
    return {"updatedAt": data.get("updatedAt", ""), "models": data.get("models", [])}


def list_local_models() -> list[dict]:
    """本地模型清单。

    以 manifest 为准；若 manifest 缺失但目录里确有已下载的权重（例如手动放置），
    则按目录名兜底列出，避免「文件在、页面不显示」的割裂体验。
    """
    models = read_manifest()["models"]
    known = {m["modelId"] for m in models}
    root = models_dir()
    if root.exists():
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            if folder.name in known:
                continue
            weight = next((folder / n for n in WEIGHT_FILES if (folder / n).exists()), None)
            if weight is None:
                continue
            models.append(
                {
                    "modelId": folder.name,
                    "repo": "",
                    "kind": "embedding",
                    "label": folder.name,
                    "dimensions": None,
                    "path": str(folder.relative_to(root.parent.parent)).replace("\\", "/"),
                    "sizeMB": round(weight.stat().st_size / 1024 / 1024, 1),
                    "downloadedAt": "",
                    "desc": "手动放置的模型（未登记在 manifest）",
                }
            )
    return models


def resolve(model_id: str = "") -> dict:
    """按 model_id 取本地模型元信息；空 id 取第一个可用模型。"""
    models = list_local_models()
    if not models:
        raise RuntimeError(
            "本地模型未下载：请执行 python scripts/download_models.py（权重落在 apps/api/models/）"
        )
    if not model_id:
        return models[0]
    for m in models:
        if m["modelId"] == model_id:
            return m
    raise RuntimeError(f"本地模型不存在：{model_id}（可用：{', '.join(m['modelId'] for m in models)}）")


def model_folder(meta: dict) -> Path:
    folder = models_dir() / meta["modelId"]
    if not any((folder / n).exists() for n in WEIGHT_FILES):
        raise RuntimeError(f"本地模型权重缺失：{folder}")
    return folder


def _load(model_id: str = "") -> tuple[dict, object, float]:
    """懒加载 SentenceTransformer（进程内单例），返回 (元信息, 模型, 加载耗时秒)。"""
    meta = resolve(model_id)
    cached = _LOADED.get(meta["modelId"])
    if cached is not None:
        return meta, cached[1], cached[0]
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # 依赖未装时给出可执行指令，而不是 500 堆栈
        raise RuntimeError(
            "未安装本地推理依赖：pip install sentence-transformers（已装则检查虚拟环境）"
        ) from exc

    folder = model_folder(meta)
    started = time.time()
    try:
        import torch

        torch.set_num_threads(settings.local_model_threads)
    except ImportError:  # torch 缺失时由 SentenceTransformer 报更准确的错
        pass
    # local_files_only=True：只读本地目录，不去 hub 校验版本（离线环境也能起）
    model = SentenceTransformer(str(folder), local_files_only=True)
    elapsed = time.time() - started
    _LOADED[meta["modelId"]] = (elapsed, model)
    return meta, model, elapsed


def embed(texts: list[str], model_id: str = "") -> dict:
    """对文本做向量化，返回 `{modelId, dimensions, count, latencyMs, loadSec, vectors}`。

    BGE 中文系列在非对称检索场景建议对 query 加前缀「为这个句子生成表示以用于检索相关文章：」，
    出题去重属对称相似度比较，统一不加前缀，保证同一口径。
    """
    meta, model, load_sec = _load(model_id)
    started = time.time()
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    matrix = [v.tolist() for v in vectors]
    return {
        "modelId": meta["modelId"],
        "dimensions": len(matrix[0]) if matrix else meta.get("dimensions"),
        "count": len(matrix),
        "latencyMs": int((time.time() - started) * 1000),
        "loadSec": round(load_sec, 1),
        "vectors": matrix,
    }


def selftest(model_id: str = "") -> dict:
    """连通性自测：真跑一次向量化，回传真实延迟与维度（非 Mock）。

    额外做一条相似度合理性校验：同义句相似度应显著高于无关句，
    否则说明权重与维度对不上（比如手动换了模型但没改 params.dimensions）。
    """
    pair = ["如何设计一个高并发秒杀系统", "怎么设计高并发的秒杀系统"]
    unrelated = "你平时喜欢做什么运动"
    result = embed([*pair, unrelated], model_id)
    cos = lambda a, b: sum(x * y for x, y in zip(a, b))  # noqa: E731  向量已归一化，点积即余弦
    sim_close = cos(result["vectors"][0], result["vectors"][1])
    sim_far = cos(result["vectors"][0], result["vectors"][2])

    sane = sim_close > sim_far + 0.1
    detail = (
        f'本地推理 dim {result["dimensions"]} / {result["count"]} 条样本 '
        f'{result["latencyMs"]}ms（首次加载 {result["loadSec"]}s）；'
        f'同义句相似度 {sim_close:.2f} vs 无关句 {sim_far:.2f}'
        + ("" if sane else "（异常：向量区分度不足，请核对权重与维度配置）")
    )
    return {
        "ok": sane,
        "modelId": result["modelId"],
        "dimensions": result["dimensions"],
        "latencyMs": result["latencyMs"],
        "detail": detail,
        "error": None if sane else "向量区分度不足，请核对本地模型权重与 params.dimensions",
    }
