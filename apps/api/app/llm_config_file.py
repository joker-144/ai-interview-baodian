"""管理端模型配置的落盘持久化（apps/api/config/llm.json）。

为什么从内存改为落文件：
- 配置与 API Key 必须在重启后保留；纯内存实现重启即失，管理端每次都要重填；
- 单文件 JSON 足够一期单机部署；集群部署时迁移到 PostgreSQL（见 admin.py 说明），
  本模块即读写边界，届时只需替换本模块实现。

约定：
- `config/llm.json.example` 是**模板**（API Key 全空），入仓供团队对齐字段结构；
- `config/llm.json` 是真实配置（Key 为混淆串，非明文），被 .gitignore 忽略不入仓；
- 启动时 llm.json 缺失则从 example 复制生成；字段 / 分层缺失以 store 种子补齐；
- 每次配置 / 历史 / 审计变更后整文件原子重写（先写 .tmp 再 replace），
  避免进程中断写出半个 JSON。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# 相对项目结构计算（app/ 的上一级即 apps/api/），不硬编码绝对路径
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
CONFIG_PATH = CONFIG_DIR / "llm.json"
EXAMPLE_PATH = CONFIG_DIR / "llm.json.example"


def load() -> dict[str, Any] | None:
    """读取持久化配置；文件缺失 / 损坏返回 None（由调用方用种子兜底）。"""
    if not CONFIG_PATH.exists():
        _init_from_example()
    if not CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("configs"), dict):
        return None
    return data


def save(configs: dict[str, Any], history: dict[str, Any], audit: list[dict[str, Any]]) -> None:
    """原子重写配置文件（configs / history / audit 全量快照）。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"configs": configs, "history": history, "audit": audit}
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


def _init_from_example() -> None:
    """首次启动：从模板复制出真实配置文件（模板缺失时静默跳过，走种子兜底）。"""
    if not EXAMPLE_PATH.exists():
        return
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
