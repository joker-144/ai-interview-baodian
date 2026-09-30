"""BOSS 直聘专用 Chrome（CDP）生命周期管理 —— 引擎 B 浏览器通道的落地策略。

## 为什么是「常驻真实浏览器」而不是 headless

zhipin 反爬决定：**认证检索必须由一个交互式登录过的真实浏览器发起**（`__zp_stoken__`
由页面 JS 生成，只向通过安全校验的真实会话下发数据）。纯 headless 一次性浏览器不可行
——实测带登录态时其 API fetch 永久挂起（详见 README「运行期发现」）。这一点在成熟同类
项目 BossHunter（github.com/shengjidaguai-china/BossHunter）里也是一致结论：它同样用
`patchright + connect_over_cdp` 复用一个常驻的真实 Chrome。

## 策略：不追求「零常驻」，而把「常驻」变省心（借鉴 BossHunter）

- **持久化专用 profile**（`--user-data-dir`，默认 `%LOCALAPPDATA%\\AiInterviewChrome`）：
  登录一次，Cookie 长期保存，之后重启 Chrome 免重复登录；与日常浏览器隔离，互不干扰。
- **`--remote-debugging-port=9222`**：boss-agent-cli 的 `auto` 通道会自动探测并复用该已
  登录 context（`connect_over_cdp` + 复用 `contexts[0]`），无需我方改动取数链路。
- **检索前健康预检**：探测本地 `/json/version`（不触达 zhipin、无风控风险）；未就绪则
  **快速失败**并给出「运行启动器」的明确指引，避免 boss-agent-cli 降级 headless 后长挂起。

本模块只负责「启动 + 探测 + 状态」，不改动 boss-agent-cli 的取数逻辑。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

# CDP 端点（须与专用 Chrome 的 --remote-debugging-port 一致；boss-agent-cli 默认探测 9222）
CDP_HOST = "127.0.0.1"
CDP_PORT = int(os.getenv("BOSS_CDP_PORT", "9222"))
ZHIPIN_HOME = "https://www.zhipin.com/"
# 拉起 Chrome 后轮询 CDP 就绪的最长等待
READY_TIMEOUT_SEC = 15.0
# 专用持久化 profile：可用 BOSS_CHROME_PROFILE 覆盖；默认落在 LOCALAPPDATA 下与日常浏览器隔离
_PROFILE_ENV = "BOSS_CHROME_PROFILE"
_DEFAULT_PROFILE_NAME = "AiInterviewChrome"
# Windows 启动脚本（相对仓库根），用于给出手动启动指引
_WIN_LAUNCHER = "apps/api/scripts/start_boss_chrome.ps1"


def cdp_version_url() -> str:
    return f"http://{CDP_HOST}:{CDP_PORT}/json/version"


def _default_profile_dir() -> Path:
    base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or str(Path.home() / ".cache")
    return Path(base) / _DEFAULT_PROFILE_NAME


def profile_dir() -> Path:
    """专用持久化 profile 目录（登录态保存在此，登录一次即可长期复用）。"""
    override = os.getenv(_PROFILE_ENV)
    return Path(override).expanduser() if override else _default_profile_dir()


def preflight_enabled() -> bool:
    """检索前 CDP 预检开关（默认开）；设 BOSS_SKIP_CDP_PREFLIGHT=1 可关闭（如用非 9222 的自定义通道）。"""
    return os.getenv("BOSS_SKIP_CDP_PREFLIGHT", "").strip().lower() not in ("1", "true", "yes", "on")


def cdp_reachable(timeout: float = 2.0) -> bool:
    """探测专用 Chrome 的 CDP 端点是否就绪。

    纯本地 HTTP 探测（127.0.0.1:9222/json/version），**不触达 zhipin、无风控风险**；
    端口无监听时立即 ECONNREFUSED 返回 False，开销可忽略。
    """
    try:
        with urllib.request.urlopen(cdp_version_url(), timeout=timeout) as resp:  # noqa: S310 (固定本地 http)
            return resp.getcode() == 200
    except Exception:
        return False


def find_chrome_exe() -> str | None:
    """定位 Chromium 系可执行文件：Chrome 优先，Windows 下回退 Edge（同为 Chromium，支持 CDP）。"""
    candidates: list[str] = []
    if sys.platform == "win32":
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.getenv(env)
            if base:
                candidates.append(str(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"))
        for name in ("chrome", "msedge"):
            found = shutil.which(name)
            if found:
                candidates.append(found)
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.getenv(env)
            if base:
                candidates.append(str(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"))
    elif sys.platform == "darwin":
        candidates.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            found = shutil.which(name)
            if found:
                candidates.append(found)
    for path in candidates:
        try:
            if path and Path(path).exists():
                return path
        except OSError:
            continue
    return None


def chrome_launch_args(profile: Path, port: int, url: str = ZHIPIN_HOME) -> list[str]:
    """专用 Chrome 启动参数：远程调试端口 + 持久化 profile + 打开 BOSS 直聘。"""
    return [
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]


def manual_start_hint() -> str:
    """给真人的一句话启动指引（前端引导卡 / 错误 recovery 用）。"""
    if sys.platform == "win32":
        return f"powershell -ExecutionPolicy Bypass -File {_WIN_LAUNCHER}"
    chrome = find_chrome_exe() or "google-chrome"
    return f'"{chrome}" --remote-debugging-port={CDP_PORT} --user-data-dir="{profile_dir()}" {ZHIPIN_HOME}'


def launch_dedicated_chrome(url: str = ZHIPIN_HOME, wait_ready: bool = True) -> dict[str, Any]:
    """拉起（或复用）专用持久化 profile 的 Chrome，并轮询 CDP 就绪。

    - CDP 已可达：直接复用，不重复启动；
    - 以脱离方式启动（Windows DETACHED_PROCESS / Unix 新会话），Chrome 独立于后端进程存活；
    - 登录态保存在 profile 目录，**登录一次**即可长期复用。

    仅在用户显式触发（前端「启动浏览器」按钮 / 运行启动脚本）时调用，后端不会自动 spawn。
    """
    if cdp_reachable():
        return {"launched": False, "reachable": True, "port": CDP_PORT,
                "detail": "专用 Chrome 已在运行（CDP 就绪），直接复用当前登录态"}

    chrome = find_chrome_exe()
    profile = profile_dir()
    if not chrome:
        return {"launched": False, "reachable": False, "chromeFound": False, "port": CDP_PORT,
                "profileDir": str(profile), "startCommand": manual_start_hint(),
                "detail": "未找到 Chrome 可执行文件：请安装 Google Chrome，或手动以 "
                          f"--remote-debugging-port={CDP_PORT} 启动一个已登录 BOSS 直聘的浏览器"}

    try:
        profile.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {"close_fds": True, "stdin": subprocess.DEVNULL,
                                  "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = (
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([chrome, *chrome_launch_args(profile, CDP_PORT, url)], **kwargs)
    except OSError as exc:
        return {"launched": False, "reachable": False, "chromeFound": True, "port": CDP_PORT,
                "profileDir": str(profile), "startCommand": manual_start_hint(),
                "detail": f"启动 Chrome 失败：{exc}"}

    reachable = False
    if wait_ready:
        deadline = time.monotonic() + READY_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if cdp_reachable(timeout=1.0):
                reachable = True
                break
            time.sleep(0.5)

    return {
        "launched": True, "reachable": reachable, "chromeFound": True, "port": CDP_PORT,
        "chromePath": chrome, "profileDir": str(profile), "startCommand": manual_start_hint(),
        "detail": (
            "专用 Chrome 已启动：请在打开的窗口中登录 BOSS 直聘（登录一次，profile 会持久保存，之后免重复登录）"
            if reachable else
            "Chrome 已启动但 CDP 端点未在超时内就绪：请确认 9222 端口未被占用后重试"
        ),
    }


def browser_status() -> dict[str, Any]:
    """浏览器连接态（供 /api/jobs/status 与检索预检复用）：本地探测，无风控风险。"""
    connected = cdp_reachable()
    return {
        "connected": connected,
        "port": CDP_PORT,
        "profileDir": str(profile_dir()),
        "chromeFound": bool(find_chrome_exe()),
        "startCommand": manual_start_hint(),
        "hint": "" if connected else (
            f"BOSS 专用浏览器未连接：检索需要一个已登录 BOSS 直聘的真实 Chrome。"
            f"运行 `{manual_start_hint()}` 启动专用 Chrome 并登录一次（登录态持久保存，之后免重复登录）"
        ),
    }
