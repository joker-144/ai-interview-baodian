"""真实 LLM 调用客户端（一期引擎 A 用）。

配置来源：管理端热生效的 `store.state.llm_config[layer]`（含混淆存储的 Key、
baseUrl、params）。统一封装超时、重试与 JSON 解析，业务侧只关心 messages。

阻塞式 HTTP 调用跑在线程池里（`asyncio.to_thread`），不阻塞 FastAPI 事件循环；
并发上限取该分层 params.concurrency。
"""

import asyncio
import json
import re
from typing import Any

import requests

from app import store
from app.keys import deobfuscate

# 单次调用重试次数（网络抖动 / 5xx）；4xx 直接抛出不重试
MAX_ATTEMPTS = 2
RETRY_BACKOFF_SEC = 1.5


class LlmError(RuntimeError):
    """调用失败（配置缺失 / 鉴权失败 / 超时 / 返回不可解析）。"""


def layer_config(layer: str) -> dict[str, Any]:
    """取分层配置并校验可用性（未启用或缺 Key 时给出可读错误）。"""
    cfg = store.state.llm_config.get(layer)
    if not cfg:
        raise LlmError(f"分层 {layer} 未配置")
    if not cfg.get("enabled"):
        raise LlmError(f"分层 {layer} 已停用，请在管理端启用")
    if cfg.get("provider") != "local" and not cfg.get("apiKey"):
        raise LlmError(f"分层 {layer} 未配置 API Key，请在管理端填写")
    if not (cfg.get("baseUrl") or "").strip():
        raise LlmError(f"分层 {layer} 未配置 baseUrl")
    return cfg


def _endpoint(cfg: dict[str, Any]) -> str:
    return cfg["baseUrl"].rstrip("/") + "/chat/completions"


def _post(cfg: dict[str, Any], body: dict[str, Any], timeout: int) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    key = deobfuscate(cfg.get("apiKey", ""))
    if key:
        headers["Authorization"] = f"Bearer {key}"

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(_endpoint(cfg), json=body, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS:
                continue
            raise LlmError(f"请求 {cfg['provider']} 失败：{exc}") from exc

        if resp.status_code >= 400:
            detail = resp.text[:300]
            # 4xx 是配置/参数问题，重试无益
            if resp.status_code < 500:
                raise LlmError(f"{cfg['provider']} 返回 {resp.status_code}：{detail}")
            last_error = LlmError(f"{cfg['provider']} 返回 {resp.status_code}：{detail}")
            if attempt < MAX_ATTEMPTS:
                continue
            raise last_error

        try:
            return resp.json()
        except ValueError as exc:
            raise LlmError(f"{cfg['provider']} 返回非 JSON 响应") from exc

    raise LlmError(str(last_error) if last_error else "调用失败")


def chat_sync(
    layer: str,
    messages: list[dict[str, str]],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: int | None = None,
) -> str:
    """同步对话调用，返回 assistant 文本。"""
    cfg = layer_config(layer)
    params = cfg.get("params") or {}
    body: dict[str, Any] = {
        "model": cfg["modelName"],
        "messages": messages,
        "temperature": params.get("temperature", 0.3) if temperature is None else temperature,
        "max_tokens": params.get("maxTokens", 4096) if max_tokens is None else max_tokens,
        # 结构化输出场景更看重稳定与吞吐
        "stream": False,
    }
    payload = _post(cfg, body, timeout or int(params.get("timeoutSec", 60)))
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmError("响应结构异常，未取到 message.content") from exc
    finish = payload["choices"][0].get("finish_reason")
    if finish == "length":
        raise LlmError("输出被 max_tokens 截断，请减小单次题量或调大 maxTokens")
    return content or ""


async def chat(layer: str, messages: list[dict[str, str]], **kwargs: Any) -> str:
    """异步对话调用（线程池执行，避免阻塞事件循环）。"""
    return await asyncio.to_thread(chat_sync, layer, messages, **kwargs)


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _strip_fence(text: str) -> str:
    match = _FENCE_RE.search(text)
    return (match.group(1) if match else text).strip()


def _first_json(text: str) -> Any:
    """容错解析：优先整体解析，失败则截取首个 JSON 对象/数组。"""
    cleaned = _strip_fence(text)
    try:
        return json.loads(cleaned)
    except ValueError:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except ValueError:
                continue
    raise LlmError("模型输出不是合法 JSON")


async def chat_json(layer: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
    """对话并解析 JSON（容忍 ```json 围栏与前后解释性文字）。"""
    return _first_json(await chat(layer, messages, **kwargs))
