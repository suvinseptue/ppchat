# Capture WeChat 4.0 SQLCipher keys from Weixin.exe memory.
# Requires: install.cmd already done, Weixin.exe logged in, Administrator.
#Requires -Version 5.1
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot
$VenvPy = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$FindKeys = Join-Path $RepoRoot "tools\find_keys_windows.py"
$BuildMap = Join-Path $RepoRoot "tools\build_keymap.py"

function Fail($msg) { Write-Host "[!] $msg" -ForegroundColor Red; if (-not $env:PPCHAT_NO_PAUSE) { Read-Host "按 Enter 关闭" }; exit 1 }

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) {
    Write-Host "[*] 正在请求管理员权限 ..." -ForegroundColor Cyan
    $arg = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    try {
        Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $arg
    } catch {
        Fail "已取消 UAC。请右键 setup-keys.cmd → 以管理员身份运行。"
    }
    exit 0
}

if (-not (Test-Path $VenvPy)) {
    Fail "还没有虚拟环境。请先运行 install.cmd。"
}

$weixin = Get-Process -Name Weixin -ErrorAction SilentlyContinue
if (-not $weixin) {
    $legacy = Get-Process -Name WeChat -ErrorAction SilentlyContinue
    if ($legacy) {
        Fail "检测到 WeChat.exe（3.x）。ppchat 只支持微信 4.0（Weixin.exe）。"
    }
    Fail "Weixin.exe 未在运行。请打开并登录微信 4.0，再重新运行 setup-keys.cmd。"
}
Write-Host "[*] Weixin.exe pid=$(( $weixin | Sort-Object WorkingSet64 -Descending | Select-Object -First 1).Id)" -ForegroundColor Cyan

Write-Host "[1/2] 扫描进程内存 -> %USERPROFILE%\.ppchat\candidates_windows.json"
& $VenvPy $FindKeys
if ($LASTEXITCODE -ne 0) {
    Fail "没有扫到密钥。请确认：管理员、微信已登录、杀软未拦截 ReadProcessMemory。部分 4.1+ 不再明文缓存密钥。"
}

$cand = Join-Path $env:USERPROFILE ".ppchat\candidates_windows.json"
if (-not (Test-Path $cand)) {
    Fail "扫描结束后未找到 $cand"
}

Write-Host "[2/2] 生成 keymap -> %USERPROFILE%\.ppchat\keys.json"
& $VenvPy $BuildMap $cand
if ($LASTEXITCODE -ne 0) {
    Fail "没有匹配到任何数据库。请检查 %USERPROFILE%\.ppchat\config.json 里的 db_root。"
}

Write-Host ""
Write-Host "==== 密钥已就绪 ====" -ForegroundColor Green
Write-Host "入库：  ppchat.cmd -c `"from ppchat import parse; print(parse.ingest())`""
Write-Host "导出：  ppchat.cmd -c `"from ppchat import app; print(app.export_day('群名','YYYY-MM-DD'))`""
if (-not $env:PPCHAT_NO_PAUSE) { Read-Host "按 Enter 关闭" }
exit 0
