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
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17": {
        "label": "SenseVoice 语音识别（中/英/日/韩/粤）",
        "kind": "asr",
        "dimensions": None,
        "sizeMB": 228,
        "desc": "本地 ASR：五期语音模拟面试离线转写，int8 权重，约 22 倍实时",
        "default": False,
        # 直连 GitHub release 的 tarball（含 fp32 894MB + int8 228MB），经 gh-proxy 加速；
        # 解压后只保留 int8 权重 + tokens + 少量自测样本，跳过 fp32 大文件
        "tarball": {
            "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
            "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2",
            "strip_prefix": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/",
            "keep": [
                "model.int8.onnx", "tokens.txt",
                "test_wavs/zh.wav", "test_wavs/en.wav",
                "LICENSE", "README.md",
            ],
        },
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

# 各 kind 的权重判定文件（manifest 与 --list 按此识别「已下载」）
WEIGHT_BY_KIND = {"embedding": WEIGHT_CANDIDATES, "asr": ["model.int8.onnx"]}

# GitHub release 大文件加速代理：直连仅 ~57kB/s，经代理实测 ~11MB/s
GITHUB_PROXY = "https://gh-proxy.com/"


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


def _weight_of(folder: Path, kind: str) -> Path | None:
    """按 kind 返回目录下已存在的权重文件（判定「已下载」与写 manifest 用）。"""
    for name in WEIGHT_BY_KIND.get(kind, WEIGHT_CANDIDATES):
        candidate = folder / name
        if candidate.exists():
            return candidate
    return None


def _download_tarball_model(model_id: str, meta: dict) -> bool:
    """下载 GitHub release 的 tarball（经 gh-proxy 加速），只解压所需文件后删包。

    用于 sherpa-onnx 语音模型：单个 .tar.bz2 内含 fp32 + int8，体积大，
    只保留 keep 列表内成员（跳过 fp32），断点续传下载、流式解压。
    """
    import tarfile

    spec = meta["tarball"]
    target = MODELS_DIR / model_id
    prefix = spec.get("strip_prefix", "")
    keep = set(spec["keep"])
    tar_path = MODELS_DIR / f"{model_id}.tar.bz2"
    print(f"[down] {model_id} -> {target.relative_to(ROOT)}（tarball 解压，仅留 int8）")
    started = time.time()

    for idx, url in enumerate([GITHUB_PROXY + spec["url"], spec["url"]]):
        label = "gh-proxy" if idx == 0 else "github 直连"
        print(f"       源：{label}")
        try:
            if not _download_file(url, tar_path):
                continue
            target.mkdir(parents=True, exist_ok=True)
            got = 0
            with tarfile.open(tar_path, "r:bz2") as tf:
                for member in tf:  # 流式遍历，边解压边跳过不需要的成员
                    if not member.isfile():
                        continue
                    rel = (
                        member.name[len(prefix):]
                        if prefix and member.name.startswith(prefix)
                        else member.name
                    )
                    if rel not in keep:
                        continue
                    src = tf.extractfile(member)
                    if src is None:
                        continue
                    dest = target / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with src, open(dest, "wb") as fh:
                        fh.write(src.read())
                    got += 1
            if got and _weight_of(target, meta["kind"]):
                tar_path.unlink(missing_ok=True)
                print(f"[done] {model_id} 用时 {time.time() - started:.1f}s（解压 {got} 个文件，源：{label}）")
                return True
            print("       解压未得到权重，换下一个源")
        except (requests.RequestException, OSError, tarfile.TarError) as exc:
            print(f"       {type(exc).__name__}: {str(exc)[:100]}，换下一个源")
    tar_path.unlink(missing_ok=True)
    print(f"[fail] {model_id} 所有下载源均失败")
    return False


def _write_manifest() -> None:
    """扫描本地目录，写出 manifest.json（管理端「本地模型」数据源）。"""
    entries = []
    for model_id, meta in MODEL_REGISTRY.items():
        folder = MODELS_DIR / model_id
        weight = _weight_of(folder, meta["kind"])
        if weight is None:
            continue
        entries.append(
            {
                "modelId": model_id,
                "repo": meta.get("repos", {}).get("hf", ""),
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
        weight = _weight_of(folder, meta["kind"])
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
    results = [
        (
            _download_tarball_model(t, MODEL_REGISTRY[t])
            if "tarball" in MODEL_REGISTRY[t]
            else _download_model(t, MODEL_REGISTRY[t], order)
        )
        for t in targets
        if t in MODEL_REGISTRY
    ]
    _write_manifest()
    if not results or not any(results):
        return 1
    print("[ok] 本地模型就绪：管理端「Embedding」层供应商选「本地模型（项目内置）」即可离线调用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
