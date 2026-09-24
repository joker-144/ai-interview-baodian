"""本地模型下载脚本（文档 4.6.1 / design-overview 第 5 节）。

设计约定：
- 权重统一落在 `apps/api/models/<model_id>/`，该目录**入仓跟踪**（团队共享、免重复下载）；
- 运行时由 `apps/api/app/local_models.py` 直接读本地目录加载，**不经过公网 API**；
- 下载成功后刷新 `apps/api/models/manifest.json`，管理端「本地模型」列表即读该清单，
  因此新增模型只需在 MODEL_REGISTRY 里加一行，前后端无需改动。

多源直连（不依赖 huggingface_hub，少一层失败面）：
- 默认顺序 modelscope → hf-mirror → huggingface，逐文件重试，单文件支持断点续传（Range）；
- 实测国内直连 huggingface.co 会超时，且 hf-mirror 的 `model.safetensors` 会 302 到
  `cas-bridge.xethub.hf.co`（同样不通），故默认优先 ModelScope CDN。

用法（在项目根目录）：
    python scripts/download_models.py                       # 下载默认清单
    python scripts/download_models.py --list                # 只查看清单与本地状态
    python scripts/download_models.py --model bge-large-zh-v1.5
    python scripts/download_models.py --source hf-mirror    # 指定下载源
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "apps" / "api" / "models"
MANIFEST = MODELS_DIR / "manifest.json"

# 下载源模板：{repo} = 仓库标识，{path} = 仓库内文件路径
SOURCES = {
    "modelscope": "https://www.modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={path}",
    "hf-mirror": "https://hf-mirror.com/{repo}/resolve/main/{path}",
    "hf": "https://huggingface.co/{repo}/resolve/main/{path}",
}
DEFAULT_ORDER = ["modelscope", "hf-mirror", "hf"]

# 可离线运行的模型清单。只收录「下载即用、无需 API Key」的模型；
# 生成式大模型（主模型 / 轻量模型 / 多模态）仍走云端供应商，见 app/providers.py。
MODEL_REGISTRY: dict[str, dict] = {
    "bge-small-zh-v1.5": {
        "label": "BGE 中文向量（小）",
        "kind": "embedding",
        "dimensions": 512,
        "sizeMB": 95,
        "desc": "默认向量模型：题目去重 / 知识点检索，CPU 可跑",
        "default": True,
        "repos": {"modelscope": "AI-ModelScope/bge-small-zh-v1.5", "hf": "BAAI/bge-small-zh-v1.5"},
    },
    "bge-large-zh-v1.5": {
        "label": "BGE 中文向量（大）",
        "kind": "embedding",
        "dimensions": 1024,
        "sizeMB": 1300,
        "desc": "高精度可选：检索召回更准，需更大内存",
        "default": False,
        "repos": {"modelscope": "AI-ModelScope/bge-large-zh-v1.5", "hf": "BAAI/bge-large-zh-v1.5"},
    },
}

# sentence-transformers 加载所需文件。required=False 的缺失可忽略（不同仓库文件不齐）。
# 权重优先 model.safetensors，缺失则回落 pytorch_model.bin（两者只需其一）。
FILES: list[tuple[str, bool]] = [
    ("config.json", True),
    ("modules.json", True),
    ("config_sentence_transformers.json", False),
    ("sentence_bert_config.json", False),
    ("1_Pooling/config.json", True),
    ("tokenizer_config.json", True),
    ("tokenizer.json", False),
    ("vocab.txt", True),
    ("special_tokens_map.json", False),
]
WEIGHT_CANDIDATES = ["model.safetensors", "pytorch_model.bin"]


def _fmt_mb(num_bytes: int) -> str:
    return f"{num_bytes / 1024 / 1024:.1f}MB"


def _download_file(url: str, dest: Path, timeout: int = 60) -> bool:
    """单文件下载，带断点续传与进度（每 10% 打一行，避免刷屏）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}

    with requests.get(url, stream=True, timeout=timeout, headers=headers) as resp:
        if resp.status_code == 416:  # Range 越界 = 已下完整
            part.replace(dest)
            return True
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        append = resp.status_code == 206 and have > 0
        total = int(resp.headers.get("Content-Length", "0")) + (have if append else 0)
        done = have if append else 0
        shown = -1
        with open(part, "ab" if append else "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                if not chunk:
                    continue
                fh.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    if pct // 10 != shown // 10:
                        shown = pct
                        print(f"       {pct:>3}%  {_fmt_mb(done)} / {_fmt_mb(total)}", flush=True)
    part.replace(dest)
    return True


def _download_model(model_id: str, meta: dict, order: list[str]) -> bool:
    target = MODELS_DIR / model_id
    print(f"[down] {model_id} -> {target.relative_to(ROOT)} (~{meta['sizeMB']}MB)")
    started = time.time()

    for source in order:
        repo = meta["repos"].get(source) or meta["repos"].get("hf")
        if not repo:
            continue
        base = SOURCES[source].format(repo=repo, path="{path}")
        print(f"       源：{source}（{repo}）")
        try:
            ok = True
            for path, required in FILES:
                dest = target / path
                if dest.exists() and dest.stat().st_size > 0:
                    continue
                if not _download_file(base.replace("{path}", path), dest):
                    if required:
                        ok = False
                        break
            if not ok:
                continue
            # 权重：两种格式任一即可，已存在则跳过
            if not any((target / w).exists() for w in WEIGHT_CANDIDATES):
                for weight in WEIGHT_CANDIDATES:
                    if _download_file(base.replace("{path}", weight), target / weight):
                        break
                else:
                    print("       权重下载失败，换下一个源")
                    continue
            if not any((target / w).exists() for w in WEIGHT_CANDIDATES):
                continue
            print(f"[done] {model_id} 用时 {time.time() - started:.1f}s（源：{source}）")
            return True
        except (requests.RequestException, OSError) as exc:
            print(f"       {type(exc).__name__}: {str(exc)[:100]}，换下一个源")
    print(f"[fail] {model_id} 所有下载源均失败")
    return False


def _write_manifest() -> None:
    """扫描本地目录，写出 manifest.json（管理端「本地模型」数据源）。"""
    entries = []
    for model_id, meta in MODEL_REGISTRY.items():
        folder = MODELS_DIR / model_id
        weight = next((folder / n for n in WEIGHT_CANDIDATES if (folder / n).exists()), None)
        if weight is None:
            continue
        entries.append(
            {
                "modelId": model_id,
                "repo": meta["repos"].get("hf", ""),
                "kind": meta["kind"],
                "label": meta["label"],
                "dimensions": meta.get("dimensions"),
                "path": str(folder.relative_to(ROOT)).replace("\\", "/"),
                "sizeMB": round(weight.stat().st_size / 1024 / 1024, 1),
                "downloadedAt": time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(weight.stat().st_mtime)
                ),
                "desc": meta["desc"],
            }
        )
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(
            {"updatedAt": time.strftime("%Y-%m-%d %H:%M:%S"), "models": entries},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[manifest] {MANIFEST.relative_to(ROOT)} -> {len(entries)} 个本地模型")


def _show_list() -> None:
    for model_id, meta in MODEL_REGISTRY.items():
        folder = MODELS_DIR / model_id
        weight = next((folder / n for n in WEIGHT_CANDIDATES if (folder / n).exists()), None)
        flag = f"已下载 {_fmt_mb(weight.stat().st_size)}" if weight else "未下载"
        star = "（默认）" if meta.get("default") else ""
        print(
            f"- {model_id:<20} [{flag}] {meta['kind']:<9} dim={meta.get('dimensions')}"
            f"{star} {meta['desc']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="下载本地模型权重到 apps/api/models/")
    parser.add_argument("--model", action="append", help="指定模型 id，可重复；缺省下载 default=True 的项")
    parser.add_argument("--list", action="store_true", help="只列出清单与本地状态")
    parser.add_argument(
        "--source",
        choices=sorted(SOURCES),
        help="指定下载源（缺省按 modelscope → hf-mirror → hf 顺序重试）",
    )
    args = parser.parse_args()

    if args.list:
        _show_list()
        _write_manifest()
        return 0

    order = [args.source] if args.source else DEFAULT_ORDER
    targets = args.model or [k for k, v in MODEL_REGISTRY.items() if v.get("default")]
    results = [_download_model(t, MODEL_REGISTRY[t], order) for t in targets if t in MODEL_REGISTRY]
    _write_manifest()
    if not results or not any(results):
        return 1
    print("[ok] 本地模型就绪：管理端「Embedding」层供应商选「本地模型（项目内置）」即可离线调用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
