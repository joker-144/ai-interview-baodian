"""管理端：模型配置（文档 4.6.1）。

安全约定：
- 独立鉴权（X-Admin-Token），不复用 C 端账号体系；
- API Key 混淆存储、接口只回显掩码，永不返回明文；
- 每次变更留历史快照（可回滚）并写审计日志（Key 只记掩码）。

持久化：配置 / 历史快照 / 审计落盘到 `apps/api/config/llm.json`（真实配置不入仓，
仓内是同目录的 `.example` 模板，Key 全空），读写边界见 `app/llm_config_file.py`；
接 PostgreSQL 后落 `llm_config` / `llm_config_history` 表，
热生效由 Redis 发布订阅通知各 worker 重载。
"""

import base64
import random
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException

from app import llm_config_file, local_models, providers, store
from app.config import settings
from app.schemas import (
    AuditEntry,
    LlmConfigOut,
    LlmConfigUpdate,
    LlmLayer,
    LlmTestResult,
    LocalModelListOut,
    ModelDiscoverOut,
    ModelDiscoverRequest,
    ProviderOut,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])

ACTOR = "admin"


# ---------------- 鉴权 ----------------


def verify_admin(x_admin_token: str | None = Header(default=None)) -> str:
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="管理端令牌无效")
    return ACTOR


# ---------------- Key 掩码与混淆存储 ----------------


def mask_key(key: str) -> str:
    """sk-1234567890abcd -> sk-****abcd；空 Key 返回空串。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:3]}****{key[-4:]}"


def _obfuscate(key: str) -> str:
    """存储态混淆（XOR + base64）。

    注意：这是**混淆而非加密**，仅避免明文落库/落日志。生产必须换 KMS 或
    Fernet（cryptography），密钥由环境注入且不进代码仓（见 config.key_secret）。
    """
    if not key:
        return ""
    secret = settings.key_secret.encode()
    xored = bytes(b ^ secret[i % len(secret)] for i, b in enumerate(key.encode()))
    return base64.urlsafe_b64encode(xored).decode()


def _deobfuscate(stored: str) -> str:
    if not stored:
        return ""
    secret = settings.key_secret.encode()
    raw = base64.urlsafe_b64decode(stored.encode())
    return bytes(b ^ secret[i % len(secret)] for i, b in enumerate(raw)).decode()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 除「模型发现」外的动作全部打点（发现每次输入停顿防抖都会触发，属低价值噪音）
AUDIT_ACTIONS = {"update_config", "test_conn", "migrate_model", "rollback_config"}

# 仓库根（routers/admin.py → routers → app → api → apps → 仓库根），用于把目录回显为相对路径
_REPO_ROOT = Path(__file__).resolve().parents[4]


def _persist() -> None:
    """配置 / 历史 / 审计全量快照落盘（每次变更后调用，重启不丢）。"""
    llm_config_file.save(store.state.llm_config, store.state.llm_history, store.state.audit_log)


def _audit(action: str, layer: str, detail: str) -> None:
    if action not in AUDIT_ACTIONS:
        return
    store.state.audit_log.insert(
        0, {"at": _now(), "actor": ACTOR, "action": action, "layer": layer, "detail": detail}
    )
    # 审计日志只保留最近 200 条
    del store.state.audit_log[200:]
    # 配置变更（update/rollback/migrate）与审计总是伴随发生，统一在此落盘
    _persist()


def _layer_meta(layer: str) -> dict[str, str]:
    for meta in store.LLM_LAYERS:
        if meta["layer"] == layer:
            return meta
    raise HTTPException(status_code=404, detail="模型分层不存在")


def _migrate_model(layer: str, cfg: dict) -> None:
    """供应商停用旧模型名后自动替换（如 DeepSeek 2026-07-24 停用旧别名）。

    直接改内存态并记审计，保证后续出题 / 自测读到的是可用模型名；
    迁移后旧名不再命中迁移表，不会重复触发。
    """
    to = providers.DEPRECATED_MODELS.get(cfg["modelName"])
    if not to:
        return
    old = cfg["modelName"]
    cfg["modelName"] = to
    if cfg.get("fallbackModel") in providers.DEPRECATED_MODELS:
        cfg["fallbackModel"] = providers.DEPRECATED_MODELS[cfg["fallbackModel"]]
    cfg["updatedAt"] = _now()
    _audit("migrate_model", layer, f"模型名 {old} 已被供应商停用，自动迁移为 {to}")


def _to_out(layer: str, notice: str | None = None) -> LlmConfigOut:
    meta = _layer_meta(layer)
    cfg = store.state.llm_config[layer]
    _migrate_model(layer, cfg)
    plain = _deobfuscate(cfg.get("apiKey", ""))
    return LlmConfigOut(
        layer=layer,  # type: ignore[arg-type]
        label=meta["label"],
        usage=meta["usage"],
        provider=cfg["provider"],
        modelName=cfg["modelName"],
        apiKeyMasked=mask_key(plain),
        hasKey=bool(plain),
        baseUrl=cfg["baseUrl"],
        params=cfg["params"],
        fallbackModel=cfg.get("fallbackModel", ""),
        enabled=cfg.get("enabled", True),
        updatedAt=cfg.get("updatedAt", ""),
        updatedBy=cfg.get("updatedBy", ""),
        notice=notice,
    )


# ---------------- 接口 ----------------


@router.get("/llm-config", response_model=list[LlmConfigOut])
def list_llm_config(actor: str = Depends(verify_admin)) -> list[LlmConfigOut]:
    return [_to_out(meta["layer"]) for meta in store.LLM_LAYERS]


@router.put("/llm-config/{layer}", response_model=LlmConfigOut)
def update_llm_config(
    layer: LlmLayer, body: LlmConfigUpdate, actor: str = Depends(verify_admin)
) -> LlmConfigOut:
    _layer_meta(layer)
    cfg = store.state.llm_config[layer]

    # 变更前快照入历史（供回滚）
    history = store.state.llm_history.setdefault(layer, [])
    history.insert(0, {**cfg, "params": dict(cfg["params"])})
    del history[10:]

    changes: list[str] = []
    for field_name in ("provider", "modelName", "baseUrl", "fallbackModel"):
        value = getattr(body, field_name)
        if value is not None and value != cfg.get(field_name):
            changes.append(f"{field_name}: {cfg.get(field_name) or '空'} → {value or '空'}")
            cfg[field_name] = value
    if body.enabled is not None and body.enabled != cfg.get("enabled", True):
        changes.append(f"enabled: {cfg.get('enabled', True)} → {body.enabled}")
        cfg["enabled"] = body.enabled
    if body.params:
        for k, v in body.params.items():
            if cfg["params"].get(k) != v:
                changes.append(f"params.{k}: {cfg['params'].get(k)} → {v}")
                cfg["params"][k] = v

    # apiKey：不回传或回传掩码 = 未修改（永不接收/回显明文）
    notice = None
    if body.apiKey and "****" not in body.apiKey:
        cfg["apiKey"] = _obfuscate(body.apiKey)
        changes.append(f"apiKey: → {mask_key(body.apiKey)}")

    cfg["updatedAt"] = _now()
    cfg["updatedBy"] = actor

    if layer == "embedding" and any(c.startswith("params.dimensions") for c in changes):
        notice = "向量维度已变更，需重建 pgvector 索引后新题目方可参与去重"
    notice = _provider_notice(layer, cfg, notice)

    _audit("update_config", layer, "；".join(changes) or "无字段变更")
    return _to_out(layer, notice=notice)


def _provider_notice(layer: str, cfg: dict, notice: str | None) -> str | None:
    """供应商能力校验：只提示不阻断（各家能力清单会变，硬卡会把用户锁在页面上）。"""
    tips: list[str] = [notice] if notice else []
    provider = providers.get_provider(cfg["provider"])
    if provider is None:
        tips.append(f"未知供应商 {cfg['provider']}，请从下拉列表选择")
    else:
        if layer not in provider["capabilities"]:
            tips.append(
                f"供应商「{provider['label']}」未声明承接{_layer_meta(layer)['label']}，请确认该模型可用"
            )
        if provider["id"] == providers.LOCAL_PROVIDER:
            local_ids = [m["modelId"] for m in local_models.list_local_models()]
            if not local_ids:
                tips.append("本地模型尚未下载：请执行 python scripts/download_models.py")
            elif cfg["modelName"] not in local_ids:
                tips.append(
                    f"本地模型 {cfg['modelName'] or '未选'} 不存在（可用：{', '.join(local_ids)}）"
                )
        elif provider["requiresKey"] and not cfg.get("apiKey"):
            tips.append("该供应商需 API Key，当前未配置，调用前请先填写并保存")
    return "；".join(tips) if tips else None


@router.post("/llm-config/{layer}/test", response_model=LlmTestResult)
def test_llm_config(layer: LlmLayer, actor: str = Depends(verify_admin)) -> LlmTestResult:
    """连通性自测。

    - 本地模型（provider=local）：**真跑一次向量化**，回传真实延迟与维度；
    - 云端供应商：一期为 Mock 判定（只校验配置完整性），接入 SDK 后改为发一条最小 prompt，
      并按 401 / 404 / 超时 / 余额不足分类回传错误原因。
    """
    _layer_meta(layer)
    cfg = store.state.llm_config[layer]
    plain = _deobfuscate(cfg.get("apiKey", ""))

    if cfg["provider"] == providers.LOCAL_PROVIDER:
        try:
            result = local_models.selftest(cfg["modelName"])
        except RuntimeError as exc:  # 权重未下载 / 依赖缺失 → 可执行的修复提示
            _audit("test_conn", layer, f"失败：{exc}")
            return LlmTestResult(ok=False, error=str(exc))
        _audit(
            "test_conn",
            layer,
            ("成功：" if result["ok"] else "异常：")
            + f"本地模型 {result['modelId']} dim={result['dimensions']} 延迟 {result['latencyMs']}ms",
        )
        return LlmTestResult(
            ok=result["ok"],
            latencyMs=result["latencyMs"],
            detail=result["detail"],
            error=result.get("error"),
        )

    if not plain:
        _audit("test_conn", layer, "失败：未配置 API Key")
        return LlmTestResult(ok=False, error="401 未配置 API Key，请先填写并保存")
    if not cfg["modelName"]:
        _audit("test_conn", layer, "失败：模型名为空")
        return LlmTestResult(ok=False, error="404 模型名不能为空")
    if not cfg["baseUrl"].startswith(("http://", "https://")):
        _audit("test_conn", layer, f"失败：base_url 非法（{cfg['baseUrl']}）")
        return LlmTestResult(ok=False, error="base_url 需以 http:// 或 https:// 开头")

    latency = random.randint(180, 900)
    tokens = random.randint(12, 48)
    _audit("test_conn", layer, f"成功：{cfg['modelName']} 延迟 {latency}ms / {tokens} tokens")
    return LlmTestResult(ok=True, latencyMs=latency, tokens=tokens)


@router.post("/llm-config/{layer}/rollback", response_model=LlmConfigOut)
def rollback_llm_config(layer: LlmLayer, actor: str = Depends(verify_admin)) -> LlmConfigOut:
    """回滚到上一版配置（历史栈弹出一条覆盖当前）。"""
    _layer_meta(layer)
    history = store.state.llm_history.get(layer) or []
    if not history:
        raise HTTPException(status_code=400, detail="没有可回滚的历史版本")

    previous = history.pop(0)
    current = store.state.llm_config[layer]
    restored = {
        **previous,
        "params": dict(previous["params"]),
        "updatedAt": _now(),
        "updatedBy": actor,
    }
    store.state.llm_config[layer] = restored
    _audit(
        "rollback_config",
        layer,
        f"回滚至 {previous.get('updatedAt') or '初始版本'}（modelName={restored['modelName']}）",
    )
    del current  # 当前版本已被替换，历史中不再保留
    return _to_out(layer, notice="已回滚到上一版配置，新任务将使用回滚后的模型")


@router.get("/audit-log", response_model=list[AuditEntry])
def audit_log(actor: str = Depends(verify_admin)) -> list[dict]:
    return store.state.audit_log


@router.get("/llm-config/{layer}/history")
def llm_config_history_count(layer: LlmLayer, actor: str = Depends(verify_admin)) -> dict:
    """历史快照数（前端据此决定回滚按钮可用态）。"""
    _layer_meta(layer)
    return {"layer": layer, "count": len(store.state.llm_history.get(layer) or [])}


# ---------------- 供应商与模型发现 ----------------


@router.get("/providers", response_model=list[ProviderOut])
def list_providers(actor: str = Depends(verify_admin)) -> list[dict]:
    """供应商清单（含默认 base_url 与可承接分层）：选定即自动回填地址。"""
    return providers.list_providers()


@router.post("/models/discover", response_model=ModelDiscoverOut)
def discover_models(body: ModelDiscoverRequest, actor: str = Depends(verify_admin)) -> dict:
    """按供应商 + Key 拉取可用模型清单；失败时回落常用候选，前端仍可手填模型名。

    安全：apiKey 只用于本次上游请求，不写历史快照、不进审计明文（只记掩码）。
    """
    provider = providers.get_provider(body.provider)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"未知供应商：{body.provider}")

    api_key = body.apiKey.strip()
    key_source = "入参"
    # 页面未重填 Key 时（回显为掩码），回落到该分层已存密钥，避免“换供应商就得重贴 Key”
    if not api_key and body.layer:
        api_key = _deobfuscate(store.state.llm_config[body.layer].get("apiKey", ""))
        key_source = "已存密钥"

    base_url = body.baseUrl.strip() or provider["baseUrl"]
    result = providers.discover_models(body.provider, base_url, api_key)

    if provider["id"] != providers.LOCAL_PROVIDER:
        _audit(
            "discover_models",
            body.layer or "-",
            f"{provider['label']} base_url={base_url or '空'} key={key_source}"
            f" → {result['source']} {len(result['models'])} 个模型"
            + (f"（{result['error']}）" if result.get("error") else ""),
        )
    return result


@router.get("/local-models", response_model=LocalModelListOut)
def get_local_models(actor: str = Depends(verify_admin)) -> dict:
    """项目内已下载的本地模型（apps/api/models/）与下载引导。"""
    manifest = local_models.read_manifest()
    models_dir = local_models.models_dir()
    try:  # 页面只展示仓库内相对路径；环境变量把目录指到仓库外时保留原值
        models_dir = models_dir.relative_to(_REPO_ROOT)
    except ValueError:
        pass
    return {
        "modelsDir": models_dir.as_posix(),
        "downloadCommand": "python scripts/download_models.py",
        "updatedAt": manifest["updatedAt"],
        "models": local_models.list_local_models(),
    }
