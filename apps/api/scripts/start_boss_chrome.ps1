<#
.SYNOPSIS
    启动 BOSS 直聘「专用 Chrome」（持久化 profile + 远程调试 9222）—— 引擎 B 检索的浏览器通道。

.DESCRIPTION
    策略借鉴 BossHunter（github.com/shengjidaguai-china/BossHunter）：
    zhipin 反爬决定「认证检索必须有一个交互式登录过的真实浏览器」，纯 headless 不可行。
    故不追求「零常驻浏览器」，而用「持久化专用 profile」把常驻变省心：
      - 登录一次：Cookie 保存在专用 profile（默认 %LOCALAPPDATA%\AiInterviewChrome），
        之后重启 Chrome 免重复登录；与日常浏览器隔离，互不干扰；
      - --remote-debugging-port=9222：后端 boss-agent-cli 的 auto 通道会自动探测并复用
        该已登录会话（connect_over_cdp + 复用现有 context），无需其它配置。

    脚本只做「启动 Chrome + 轮询 CDP 就绪」，不提交任何投递、不绕过平台安全机制。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\start_boss_chrome.ps1
    powershell -ExecutionPolicy Bypass -File .\start_boss_chrome.ps1 -Port 9222 -SkipNavigate

.NOTES
    端口须与后端一致（后端可用环境变量 BOSS_CDP_PORT 覆盖，默认 9222）。
#>
[CmdletBinding()]
param(
    [int]$Port = 9222,
    [string]$ProfileDir = "",
    [switch]$SkipNavigate
)

$ErrorActionPreference = "Stop"

# 专用持久化 profile：与 app/boss_browser.py 的默认值保持一致
if (-not $ProfileDir) {
    $ProfileDir = Join-Path $env:LOCALAPPDATA "AiInterviewChrome"
}

# 定位 Chrome（标准安装路径；找不到则报错，不静默降级）
$ChromeCandidates = @()
foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
    if ($base) { $ChromeCandidates += (Join-Path $base "Google\Chrome\Application\chrome.exe") }
}
$ChromeCandidates = $ChromeCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
if (-not $ChromeCandidates) {
    throw "未找到 Google Chrome。请安装 Chrome，或手动以 --remote-debugging-port=$Port 启动一个已登录 BOSS 直聘的浏览器。"
}
$Chrome = $ChromeCandidates | Select-Object -First 1

$ChromeArguments = @(
    "--remote-debugging-port=$Port",
    "--user-data-dir=$ProfileDir",
    "--no-first-run",
    "--no-default-browser-check"
)
if (-not $SkipNavigate) { $ChromeArguments += "https://www.zhipin.com/" }

Write-Host "启动 BOSS 直聘专用 Chrome（profile: $ProfileDir, port: $Port）..."
Start-Process -FilePath $Chrome -ArgumentList $ChromeArguments

# 轮询 CDP 就绪（最多 ~10s）；本地探测，不触达 zhipin
$Ready = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $null = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/version" -TimeoutSec 2
        $Ready = $true
        break
    } catch {
        # Chrome 仍在启动中
    }
}

if ($Ready) {
    Write-Host "[OK] 专用 Chrome 已就绪（CDP http://127.0.0.1:$Port）。"
    Write-Host "     请在打开的窗口中登录 BOSS 直聘 —— 登录一次即可，登录态保存在专用 profile，之后重启免重复登录。"
    Write-Host "     保持该窗口开启，后端检索会经 CDP 复用它；关闭后重新运行本脚本即可（无需再登录）。"
} else {
    Write-Warning "Chrome 远程调试在 10s 内未就绪。请确认 $Port 端口未被占用，或稍后重试。"
    exit 1
}
