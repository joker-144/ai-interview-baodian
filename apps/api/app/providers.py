"""供应商注册表与模型发现（文档 4.6.1）。

三条产品规则的实现落点：
1. **选定供应商 → base_url 自动切换**：每个供应商带默认 `baseUrl`，前端选中即回填，
   仍允许手改（兼容自建网关 / 代理 / 私有化部署）；
2. **填 Key → 自动返回可用模型**：`discover_models()` 调供应商的模型列表接口实时拉取；
   拉取失败（网络不通 / 无权限 / 接口漂移）时回落到 `staticModels` 常用候选，
   并且**永远允许手动输入模型名**（模型清单变动快，硬编码会过时）；
3. **可本地运行的模型直接下载到项目内**：`local` 供应商指向 `apps/api/models/`，
   无需 Key、不走公网，权重由 `scripts/download_models.py` 拉取，
   运行时由 `app/local_models.py` 加载（详见该模块）。

新增供应商只需在 PROVIDERS 里加一行，前后端与管理端页面均无需改动。
注意：staticModels / modelLabels 是「拉取失败时的兜底候选 + 展示名」，需与供应商官方口径同步
（如 DeepSeek 已于 2026-07 停用 deepseek-chat / deepseek-reasoner 旧别名，现行为
deepseek-flash（原生多模态）与 deepseek-v4-pro），实拉结果始终以供应商接口为准。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

LOCAL_PROVIDER = "local"

# authStyle: bearer = Authorization: Bearer <key>；anthropic = x-api-key + anthropic-version；
#            none   = 不带鉴权（本地 / Ollama）
PROVIDERS: list[dict] = [
    {
        "id": "openai",
        "label": "OpenAI",
        "kind": "cloud",
        "baseUrl": "https://api.openai.com/v1",
        "modelsPath": "/models",
        "authStyle": "bearer",
        "requiresKey": True,
        "keyPlaceholder": "sk-...",
        "capabilities": ["primary", "light", "vision", "embedding", "voice"],
        "staticModels": [
            "gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o4-mini",
            "text-embedding-3-large", "text-embedding-3-small", "whisper-1", "tts-1",
        ],
        "hint": "全能力覆盖（含语音），出海/英文场景首选",
    },
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "kind": "cloud",
        "baseUrl": "https://api.deepseek.com/v1",
        "modelsPath": "/models",
        "authStyle": "bearer",
        "requiresKey": True,
        "keyPlaceholder": "sk-...",
        "capabilities": ["primary", "light", "vision"],
        "staticModels": ["deepseek-flash", "deepseek-v4-pro"],
        # 候选面板展示用易读名（ID 是调用用的稳定标识，不能改；说明性名称随官方版本同步更新）
        "modelLabels": {
            "deepseek-flash": "DeepSeek Flash · V4.1（原生多模态：视觉 + 文本）",
            "deepseek-v4-pro": "DeepSeek V4 Pro · 旗舰（纯文本，复杂推理 / Agent）",
        },
        "hint": "deepseek-flash 原生多模态（V4.1 起）；中文出题性价比高；不提供 embedding / 语音，需与其他供应商混用",
    },
    {
        "id": "qwen",
        "label": "阿里云百炼（通义千问）",
        "kind": "cloud",
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "modelsPath": "/models",
        "authStyle": "bearer",
        "requiresKey": True,
        "keyPlaceholder": "sk-...",
        "capabilities": ["primary", "light", "vision", "embedding"],
        "staticModels": [
            "qwen-max", "qwen-plus", "qwen-turbo", "qwen-vl-max",
            "text-embedding-v4", "text-embedding-v3",
        ],
        "hint": "OpenAI 兼容模式；国内直连稳定，中文语料强",
    },
    {
        "id": "zhipu",
        "label": "智谱 AI",
        "kind": "cloud",
        "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
        "modelsPath": "/models",
        "authStyle": "bearer",
        "requiresKey": True,
        "keyPlaceholder": "填写控制台 API Key",
        "capabilities": ["primary", "light", "vision", "embedding"],
        "staticModels": ["glm-4-plus", "glm-4-air", "glm-4v-plus", "embedding-3"],
        "hint": "GLM 系列，含多模态与向量模型",
    },
    {
        "id": "moonshot",
        "label": "Moonshot（Kimi）",
        "kind": "cloud",
        "baseUrl": "https://api.moonshot.cn/v1",
        "modelsPath": "/models",
        "authStyle": "bearer",
        "requiresKey": True,
        "keyPlaceholder": "sk-...",
        "capabilities": ["primary", "light", "vision"],
        "staticModels": ["kimi-latest", "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "hint": "长上下文见长，适合整份简历/JD 一次喂入",
    },
    {
        "id": "gemini",
        "label": "Google Gemini",
        "kind": "cloud",
        "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai",
        "modelsPath": "/models",
        "authStyle": "bearer",
        "requiresKey": True,
        "keyPlaceholder": "AIza...",
        "capabilities": ["primary", "light", "vision", "embedding"],
        "staticModels": ["gemini-2.5-pro", "gemini-2.5-flash", "text-embedding-004"],
        "hint": "走 OpenAI 兼容入口；多模态与超长上下文",
    },
    {
        "id": "anthropic",
        "label": "Anthropic（Claude）",
        "kind": "cloud",
        "baseUrl": "https://api.anthropic.com/v1",
        "modelsPath": "/models",
        "authStyle": "anthropic",
        "requiresKey": True,
        "keyPlaceholder": "sk-ant-...",
        "capabilities": ["primary", "light", "vision"],
        "staticModels": ["claude-sonnet-4-5", "claude-3-5-haiku-latest"],
        "hint": "鉴权头与 OpenAI 不同（x-api-key + anthropic-version），已内置适配",
    },
    {
        "id": "siliconflow",
        "label": "硅基流动 SiliconFlow",
        "kind": "cloud",
        "baseUrl": "https://api.siliconflow.cn/v1",
        "modelsPath": "/models",
        "authStyle": "bearer",
        "requiresKey": True,
        "keyPlaceholder": "sk-...",
        "capabilities": ["primary", "light", "vision", "embedding"],
        "staticModels": [
            "deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct",
            "Qwen/Qwen2-VL-72B-Instruct", "BAAI/bge-m3",
        ],
        "hint": "聚合多家开源模型，按量计费，embedding 便宜",
    },
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "kind": "cloud",
        "baseUrl": "https://openrouter.ai/api/v1",
        "modelsPath": "/models",
        "authStyle": "bearer",
        "requiresKey": True,
        "keyPlaceholder": "sk-or-...",
        "capabilities": ["primary", "light", "vision"],
        "staticModels": ["openai/gpt-4o", "anthropic/claude-sonnet-4.5", "deepseek/deepseek-chat"],
        "hint": "一个 Key 打通多家模型，模型名带厂商前缀",
    },
    {
        "id": "ollama",
        "label": "Ollama（本机自托管）",
        "kind": "selfhost",
        "baseUrl": "http://localhost:11434/v1",
        "modelsPath": "/models",
        "authStyle": "none",
        "requiresKey": False,
        "keyPlaceholder": "无需 Key",
        "capabilities": ["primary", "light", "vision", "embedding"],
        "staticModels": ["qwen2.5:7b", "llama3.1:8b", "nomic-embed-text", "bge-m3"],
        "hint": "需本机已 `ollama serve`；模型名即 `ollama list` 中的名称",
    },
    {
        "id": LOCAL_PROVIDER,
        "label": "本地模型（项目内置）",
        "kind": "local",
        "baseUrl": "",
        "modelsPath": "",
        "authStyle": "none",
        "requiresKey": False,
        "keyPlaceholder": "无需 Key",
        "capabilities": ["embedding"],
        "staticModels": [],
        "hint": "权重下载到 apps/api/models/ 后离线推理，零 API 成本；当前支持向量模型",
    },
    {
        "id": "custom",
        "label": "自定义（OpenAI 兼容）",
        "kind": "custom",
        "baseUrl": "",
        "modelsPath": "/models",
        "authStyle": "bearer",
        "requiresKey": True,
        "keyPlaceholder": "按服务方要求填写",
        "capabilities": ["primary", "light", "vision", "embedding", "voice"],
        "staticModels": [],
        "hint": "私有化网关 / vLLM / One-API 等，需手动填 base_url",
    },
]

DISCOVER_TIMEOUT = 8  # 秒：拉取模型列表的超时，避免管理端页面卡死

# 已停用模型 ID 的迁移表（与前端 mock-data.ts 的 DEPRECATED_MODELS 同构）：
# 供应商停用旧名后，读取已保存配置时自动替换，否则页面长期「无匹配候选」且调用旧名必然 404。
# 背景：DeepSeek 官方 2026-04-24 公告，deepseek-chat / deepseek-reasoner 于 2026-07-24 停用。
DEPRECATED_MODELS: dict[str, str] = {
    "deepseek-chat": "deepseek-flash",
    "deepseek-reasoner": "deepseek-v4-pro",
    "deepseek-v4-flash": "deepseek-flash",
    "deepseek-v4-flash-vision-exp": "deepseek-flash",
}


def get_provider(provider_id: str) -> dict | None:
    return next((p for p in PROVIDERS if p["id"] == provider_id), None)


def list_providers() -> list[dict]:
    """对外输出的供应商清单（不含 staticModels 明细，模型走 discover 接口）。"""
    return [
        {
            "id": p["id"],
            "label": p["label"],
            "kind": p["kind"],
            "baseUrl": p["baseUrl"],
            "requiresKey": p["requiresKey"],
            "keyPlaceholder": p["keyPlaceholder"],
            "capabilities": p["capabilities"],
            "hint": p["hint"],
        }
        for p in PROVIDERS
    ]


def _local_models() -> list[dict]:
    """读取本地模型清单（由 scripts/download_models.py 生成 manifest.json）。"""
    from app import local_models

    return [
        {"id": m["modelId"], "label": f'{m["label"]}（dim {m["dimensions"]}）', "kind": m["kind"]}
        for m in local_models.list_local_models()
    ]


def _fetch_remote(provider: dict, base_url: str, api_key: str) -> list[dict]:
    """调供应商模型列表接口（OpenAI 风格 `{"data":[{"id":...}]}`）。

    异常一律抛出，由 discover_models 统一转成「失败 + 兜底候选」，
    保证管理端页面永远有可用的模型输入路径。
    """
    url = base_url.rstrip("/") + (provider["modelsPath"] or "/models")
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    if provider["authStyle"] == "bearer":
        req.add_header("Authorization", f"Bearer {api_key}")
    elif provider["authStyle"] == "anthropic":
        req.add_header("x-api-key", api_key)
        req.add_header("anthropic-version", "2023-06-01")

    with urllib.request.urlopen(req, timeout=DISCOVER_TIMEOUT) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    data = payload.get("data") or payload.get("models") or []
    models = []
    for item in data:
        if isinstance(item, str):  # 部分网关直接返回字符串数组
            models.append({"id": item, "label": item})
            continue
        model_id = item.get("id") or item.get("name") or ""
        if not model_id:
            continue
        models.append(
            {
                "id": model_id,
                "label": item.get("display_name") or model_id,
                "ownedBy": item.get("owned_by") or item.get("root") or "",
            }
        )
    return models


def discover_models(provider_id: str, base_url: str = "", api_key: str = "") -> dict:
    """按供应商拉取可用模型清单。

    返回 `{ok, source, models, error}`，`source` 取值：
    - `remote`：实时拉取成功（最准）；
    - `local` ：本地模型清单（读 manifest）；
    - `static`：拉取失败，回落常用候选（前端应提示可手动输入）；
    - `none`  ：无任何候选（custom 供应商未拉取时），需手动输入模型名。
    """
    provider = get_provider(provider_id)
    if provider is None:
        return {"ok": False, "source": "none", "models": [], "error": f"未知供应商：{provider_id}"}

    if provider_id == LOCAL_PROVIDER:
        models = _local_models()
        if not models:
            return {
                "ok": False,
                "source": "local",
                "models": [],
                "error": "本地模型尚未下载，请先执行 python scripts/download_models.py",
            }
        return {"ok": True, "source": "local", "models": models, "error": None}

    # static 兜底候选带易读名：ID 是调用标识，label 帮助管理员分辨版本与能力
    labels = provider.get("modelLabels", {})
    statics = [{"id": m, "label": labels.get(m, m)} for m in provider["staticModels"]]
    if provider["requiresKey"] and not api_key:
        return {
            "ok": False,
            "source": "static" if statics else "none",
            "models": statics,
            "error": "未填写 API Key，无法实时拉取；下方为常用候选，也可手动输入模型名",
        }
    if not base_url:
        return {
            "ok": False,
            "source": "static" if statics else "none",
            "models": statics,
            "error": "base_url 为空，无法拉取模型列表",
        }

    try:
        models = _fetch_remote(provider, base_url, api_key)
    except urllib.error.HTTPError as exc:
        reason = {401: "401 API Key 无效或无权限", 403: "403 无权访问模型列表",
                  404: "404 模型列表接口不存在（base_url 可能填错）"}.get(
            exc.code, f"{exc.code} {exc.reason}"
        )
        return {
            "ok": False,
            "source": "static" if statics else "none",
            "models": statics,
            "error": f"拉取失败：{reason}；下方为常用候选，也可手动输入模型名",
        }
    except Exception as exc:  # 超时 / DNS / TLS / 解析失败
        return {
            "ok": False,
            "source": "static" if statics else "none",
            "models": statics,
            "error": f"拉取失败：{type(exc).__name__}（{exc}）；下方为常用候选，也可手动输入模型名",
        }

    if not models:
        return {
            "ok": False,
            "source": "static" if statics else "none",
            "models": statics,
            "error": "接口返回空列表，请手动输入模型名",
        }
    return {"ok": True, "source": "remote", "models": models, "error": None}
