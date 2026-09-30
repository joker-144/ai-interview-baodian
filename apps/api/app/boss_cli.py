"""boss-agent-cli 子进程封装（引擎 B 岗位检索层，三期）。

接入口径（文档 3.5 / 三期实施方案）：
- boss-agent-cli 由 `uv tool install boss-agent-cli` 安装（未装时给出安装指引，不抛栈）；
- CLI stdout 只输出 JSON 信封 `{ok, data, pagination, error, hints}`，stderr 为日志；
- 错误信封统一携带 `code + recoverable + recovery_action`，据此分类映射 HTTP 语义：
    AUTH_REQUIRED / 登录失效 -> BossAuthError（401，引导 boss login 扫码）
    风控/RISK 停止类          -> BossRiskError（423，透传恢复指令）
    超时 / 网络类             -> BossTimeoutError（504）
    CLI 未安装                -> BossMissingError（503，附安装命令）
    其余 / 信封解析失败       -> BossUpstreamError（502）
- 我侧仅只读调用 status / search / detail 三个原语，不触碰 crawl / 打招呼等动作；
  请求节流由 CLI 内置高斯延迟承担，这里不再额外加频控。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

# status 读本地登录态很快；search 带 CLI 内置节流 + 可能翻页，放宽到 90s
_STATUS_TIMEOUT = 30
_SEARCH_TIMEOUT = 90
_DETAIL_TIMEOUT = 60

_INSTALL_HINT = "boss-agent-cli 未安装：请在服务端运行 `uv tool install boss-agent-cli` 安装"
_LOGIN_HINT = "Boss 直聘登录态缺失或已失效：请在服务端运行 `boss login`，用 Boss 直聘 App 扫码登录（仅只读检索）"

# 进程级缓存的可执行文件路径（避免每次请求都扫 PATH）
_boss_exe: str | None = None
_boss_checked = False


class BossError(Exception):
    """boss-agent-cli 调用失败基类；detail 为可直接展示给用户的中文提示。"""

    status_code = 502

    def __init__(self, detail: str, recovery: str = "") -> None:
        super().__init__(detail)
        self.detail = detail
        self.recovery = recovery


class BossMissingError(BossError):
    status_code = 503


class BossAuthError(BossError):
    status_code = 401


class BossRiskError(BossError):
    status_code = 423


class BossTimeoutError(BossError):
    status_code = 504


class BossUpstreamError(BossError):
    status_code = 502


class BossBrowserError(BossError):
    """专用浏览器（CDP 9222）未就绪：检索需一个已登录 BOSS 直聘的真实 Chrome。"""

    status_code = 503


def _candidate_paths() -> list[str]:
    """boss 可执行文件探测顺序：PATH -> uv tool 默认 bin 目录。"""
    found = shutil.which("boss")
    paths = [found] if found else []
    home_bin = Path.home() / ".local" / "bin"
    for name in ("boss.exe", "boss"):
        paths.append(str(home_bin / name))
    return [p for p in paths if p]


def resolve_boss_exe() -> str | None:
    """返回 boss 可执行文件路径；未安装返回 None（进程级缓存）。"""
    global _boss_exe, _boss_checked
    if _boss_checked:
        return _boss_exe
    _boss_checked = True
    for path in _candidate_paths():
        try:
            if Path(path).exists():
                _boss_exe = path
                break
        except OSError:
            continue
    return _boss_exe


def _subprocess_flags() -> int:
    """Windows 下隐藏子进程控制台窗口（服务进程调用时不闪窗）。"""
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _utf8_env() -> dict[str, str]:
    """强制 boss 子进程用 UTF-8 写 stdout（父侧已按 utf-8 decode，两侧对齐）。

    Windows 控制台默认 gbk(cp936)：CLI 输出薪资里的非断行连字符 U+2011（如「20‑30K」）
    等非 ASCII 字符时，会在子进程内部触发 UnicodeEncodeError，导致 JSON 信封被截断/损坏、
    父侧解析失败。注入 PYTHONUTF8 + PYTHONIOENCODING 让子进程 Python 运行时统一走 UTF-8。
    """
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_boss(args: list[str], timeout: int) -> dict[str, Any]:
    """执行 boss 子命令并解析 stdout JSON 信封。

    失败统一抛 BossError 子类（含 HTTP 语义），成功返回原始信封 dict。
    """
    exe = resolve_boss_exe()
    if not exe:
        raise BossMissingError(_INSTALL_HINT, "uv tool install boss-agent-cli")
    try:
        proc = subprocess.run(
            [exe, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_subprocess_flags(),
            env=_utf8_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise BossTimeoutError(
            f"boss {' '.join(args[:1])} 执行超时（{timeout}s），请稍后重试", ""
        ) from exc
    except OSError as exc:
        raise BossUpstreamError(f"boss 调用失败：{exc}", "") from exc

    # stdout 仅 JSON 信封（不变量）；解析失败时带上 stderr 片段便于排障
    try:
        envelope = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError as exc:
        tail = (proc.stderr or "").strip()[-200:]
        raise BossUpstreamError(
            f"boss 输出解析失败（可能接口漂移）：{tail or proc.stdout[:120]!r}", ""
        ) from exc

    if not isinstance(envelope, dict):
        raise BossUpstreamError("boss 输出信封结构异常（非对象）", "")

    if envelope.get("ok"):
        return envelope

    err = envelope.get("error") or {}
    code = str(err.get("code") or "").upper() if isinstance(err, dict) else str(err).upper()
    recovery = ""
    hints = envelope.get("hints")
    if isinstance(hints, dict):
        # operator_actions 是给真人操作者的指引（扫码等），透传给前端展示
        actions = hints.get("operator_actions")
        if isinstance(actions, list) and actions:
            recovery = "；".join(str(a) for a in actions[:3])
    if isinstance(err, dict) and err.get("recovery_action"):
        recovery = recovery or str(err["recovery_action"])

    message = ""
    if isinstance(err, dict):
        message = str(err.get("message") or err.get("msg") or "")

    if "AUTH" in code:
        raise BossAuthError(_LOGIN_HINT, recovery or "boss login")
    # 风控/人机校验：除显式 RISK/SAFETY 码外，zhipin 常以数字码（36/37）或文案
    # （安全校验/滑块/verify）表示挑战；统一映射 423 并给出「到专用 Chrome 手动过校验」指引
    probe = f"{code} {message}".lower()
    if (
        "RISK" in code
        or "SAFETY" in code
        or code in {"36", "37", "38"}
        or any(k in probe for k in ("安全", "验证", "校验", "滑块", "verify", "security", "captcha"))
    ):
        raise BossRiskError(
            recovery
            or "命中 BOSS 直聘安全校验：请打开专用 Chrome 窗口手动完成校验/滑块，稍等片刻后再重试检索",
            recovery,
        )
    raise BossUpstreamError(f"boss 检索失败（{code or 'UNKNOWN'}）：{message or '请稍后重试'}", recovery)


def boss_status() -> dict[str, Any]:
    """登录态探测（boss status）：返回 {cliInstalled, loggedIn, detail}。"""
    if not resolve_boss_exe():
        return {"cliInstalled": False, "loggedIn": False, "detail": _INSTALL_HINT}
    try:
        envelope = run_boss(["status"], _STATUS_TIMEOUT)
    except BossAuthError:
        return {"cliInstalled": True, "loggedIn": False, "detail": _LOGIN_HINT}
    except BossError as exc:
        # 网络抖动等不阻塞页面：CLI 在装、登录态未知，按已登录口径放行到检索时再拦
        return {"cliInstalled": True, "loggedIn": True, "detail": f"状态探测异常：{exc.detail}"}
    data = envelope.get("data")
    logged = True
    if isinstance(data, dict) and "loggedIn" in data:
        logged = bool(data.get("loggedIn"))
    elif isinstance(data, dict) and "authenticated" in data:
        logged = bool(data.get("authenticated"))
    return {
        "cliInstalled": True,
        "loggedIn": logged,
        "detail": "" if logged else _LOGIN_HINT,
    }


def _extract_jobs(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """从信封 data 中容错抽取岗位列表（CLI 版本间结构可能为 list / {jobs} / {list}）。"""
    data = envelope.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("jobs", "list", "items", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        # data 本身是单个岗位对象时包一层，避免误判为空
        return [data] if data else []
    return []


def _ensure_browser_ready() -> None:
    """检索/详情前的 CDP 预检：专用 Chrome 未就绪则快速失败，避免 CLI 降级 headless 后长挂起。

    借鉴 BossHunter 的健康检查：先探测本地 9222（不触达 zhipin、无风控风险），未连接则直接
    给出「运行启动器」的可执行指引，而不是让 boss-agent-cli 走完 bridge→cdp→headless 后超时。
    """
    from app import boss_browser

    if not boss_browser.preflight_enabled() or boss_browser.cdp_reachable():
        return
    status = boss_browser.browser_status()
    raise BossBrowserError(
        "BOSS 专用浏览器未连接：检索需要一个已登录 BOSS 直聘的真实 Chrome（CDP 9222）",
        status.get("hint") or status.get("startCommand")
        or "运行 scripts/start_boss_chrome.ps1 启动专用 Chrome 并登录一次",
    )


def search_jobs(keyword: str, city: str = "") -> list[dict[str, Any]]:
    """关键词检索岗位列表（boss search）。"""
    _ensure_browser_ready()
    args = ["search", keyword]
    if city:
        args += ["--city", city]
    envelope = run_boss(args, _SEARCH_TIMEOUT)
    jobs = _extract_jobs(envelope)
    if not jobs:
        raise BossUpstreamError("检索完成但未返回任何岗位（可尝试更换关键词或城市）", "")
    return jobs


def job_detail(security_id: str) -> dict[str, Any]:
    """单岗位全量 JD（boss detail <security_id>）。"""
    _ensure_browser_ready()
    envelope = run_boss(["detail", security_id], _DETAIL_TIMEOUT)
    data = envelope.get("data")
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    raise BossUpstreamError("岗位详情返回结构异常（可能接口漂移）", "")


class BossLimiter:
    """进程级最小调用间隔（附加保险）：即便 CLI 节流失效，我侧也保持低频只读。

    只约束 search / detail 两类网络命令，status 为本地读不限制。
    """

    MIN_INTERVAL_SEC = 3.0

    def __init__(self) -> None:
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self.MIN_INTERVAL_SEC:
            time.sleep(self.MIN_INTERVAL_SEC - elapsed)
        self._last = time.monotonic()


limiter = BossLimiter()
