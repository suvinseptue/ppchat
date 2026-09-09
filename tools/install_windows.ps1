# ppchat Windows one-click env setup.
# Installs Python 3.10+ if missing, creates .venv, pip-installs deps,
# and writes %USERPROFILE%\.ppchat\config.json (db_root).
# Key capture is a separate step: setup-keys.cmd (WeChat must be logged in).
#Requires -Version 5.1
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot
$VenvPy = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Req = Join-Path $RepoRoot "requirements.txt"
$MinMajor = 3
$MinMinor = 10
$PyInstallerUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"

function Write-Step($n, $msg) { Write-Host "[$n] $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "[!] $msg" -ForegroundColor Red; exit 1 }

function Refresh-EnvPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Test-Python310Plus {
    param([string]$Exe, [string[]]$PrefixArgs = @())
    if (-not $Exe) { return $false }
    try {
        $allArgs = @($PrefixArgs) + @(
            "-c",
            "import sys; raise SystemExit(0 if sys.version_info >= ($MinMajor, $MinMinor) else 1)"
        )
        & $Exe @allArgs 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Get-PythonSpec {
    $specs = New-Object System.Collections.Generic.List[object]

    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($tag in @("-3.13", "-3.12", "-3.11", "-3.10", "-3")) {
            $specs.Add([pscustomobject]@{ Exe = "py"; Prefix = @($tag) })
        }
    }
    foreach ($name in @("python", "python3")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notmatch "WindowsApps") {
            $specs.Add([pscustomobject]@{ Exe = $cmd.Source; Prefix = @() })
        }
    }
    $roots = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python"),
        (Join-Path ${env:ProgramFiles} "Python312"),
        (Join-Path ${env:ProgramFiles} "Python313"),
        (Join-Path ${env:ProgramFiles} "Python311"),
        (Join-Path ${env:ProgramFiles} "Python310")
    )
    foreach ($root in $roots) {
        if (-not $root -or -not (Test-Path $root)) { continue }
        Get-ChildItem $root -Filter python.exe -Recurse -ErrorAction SilentlyContinue |
            ForEach-Object { $specs.Add([pscustomobject]@{ Exe = $_.FullName; Prefix = @() }) }
    }

    foreach ($s in $specs) {
        if (Test-Python310Plus -Exe $s.Exe -PrefixArgs $s.Prefix) {
            return $s
        }
    }
    return $null
}

function Install-Python310Plus {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "    正在用 winget 安装 Python.Python.3.12（当前用户）..."
        & winget install -e --id Python.Python.3.12 --scope user `
            --accept-package-agreements --accept-source-agreements `
            --disable-interactivity
        Refresh-EnvPath
        if (Get-PythonSpec) { return }
    } else {
        Write-Warn "未找到 winget，改为下载官网安装包。"
    }

    $installer = Join-Path $env:TEMP "ppchat-python-3.12.10-amd64.exe"
    Write-Host "    下载 $PyInstallerUrl"
    Invoke-WebRequest -Uri $PyInstallerUrl -OutFile $installer
    Write-Host "    静默安装（当前用户，写入 PATH）..."
    $p = Start-Process -FilePath $installer -Wait -PassThru -ArgumentList @(
        "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_pip=1",
        "Include_test=0", "SimpleInstall=1"
    )
    if ($p.ExitCode -ne 0) {
        Fail "Python 安装包退出码 $($p.ExitCode)。请先从 https://www.python.org/downloads/ 安装 3.10+，再重新运行 install.cmd"
    }
    Refresh-EnvPath
}

if (-not (Test-Path $Req)) {
    Fail "找不到 $RepoRoot\requirements.txt，请从仓库根目录运行 install.cmd"
}

Write-Step "1/5" "仓库目录 $RepoRoot"

Write-Step "2/5" "查找 Python >= $MinMajor.$MinMinor"
$py = Get-PythonSpec
if (-not $py) {
    Write-Warn "未找到 Python $MinMajor.$MinMinor+，开始安装。"
    Install-Python310Plus
    $py = Get-PythonSpec
}
if (-not $py) {
    Fail "仍然没有 Python $MinMajor.$MinMinor+。关掉本窗口，新开资源管理器后再双击 install.cmd（刷新 PATH）。"
}
$ver = & $py.Exe @($py.Prefix + @("-c", "import sys; print(sys.version.split()[0])"))
Write-Ok "使用 $($py.Exe) $($py.Prefix -join ' ')  ($ver)"

Write-Step "3/5" "创建虚拟环境 .venv"
if (-not (Test-Path $VenvPy)) {
    & $py.Exe @($py.Prefix + @("-m", "venv", (Join-Path $RepoRoot ".venv")))
    if ($LASTEXITCODE -ne 0) { Fail "python -m venv 失败" }
}
if (-not (Test-Path $VenvPy)) { Fail "创建后未找到 $VenvPy" }
Write-Ok $VenvPy

Write-Step "4/5" "安装依赖 pip install -r requirements.txt"
& $VenvPy -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { Fail "升级 pip 失败" }
& $VenvPy -m pip install -r $Req
if ($LASTEXITCODE -ne 0) { Fail "pip install -r requirements.txt 失败" }
Write-Ok "pycryptodome / zstandard / fastapi / uvicorn"

Write-Step "5/5" "创建工作目录并写入 config.json"
$boot = & $VenvPy -c @"
import json
from ppchat import config
info = config.bootstrap_home(db_root=config.CONTAINER)
print(json.dumps({
    'home': str(info['home']),
    'config': str(info['config']),
    'keys_json': str(info['keys_json']),
    'store_db': str(info['store_db']),
    'candidates_windows': str(info['candidates_windows']),
    'db_root': str(info['db_root']) if info['db_root'] else '',
    'db_root_exists': bool(info['db_root'] and info['db_root'].exists()),
    'account_dirs': [str(p) for p in config.account_dirs()],
}))
"@
if ($LASTEXITCODE -ne 0) { Fail "bootstrap_home 失败" }
$paths = $boot | ConvertFrom-Json
Write-Ok "工作目录      $($paths.home)"
Write-Ok "密钥缓存      $($paths.keys_json)"
Write-Ok "入库数据库    $($paths.store_db)"
Write-Ok "扫描候选      $($paths.candidates_windows)"
Write-Ok "配置文件      $($paths.config)"
Write-Ok "微信数据目录  $($paths.db_root)"
if (-not $paths.db_root_exists) {
    Write-Warn "db_root 还不存在。若微信数据在别的盘，请改 config.json："
    Write-Warn "  {`"db_root`": `"D:\\path\\to\\xwechat_files`"}"
}
$accounts = @($paths.account_dirs | Where-Object { $_ })
if ($accounts.Count -eq 0) {
    Write-Warn "db_root 下还没有带 db_storage 的账号目录（本机尚未装微信时可以忽略）"
} else {
    foreach ($d in $accounts) { Write-Ok "账号目录      $d" }
}

& $VenvPy -c "from Crypto.Cipher import AES; import zstandard, fastapi, uvicorn; from ppchat import parse, app, config; print('self-check ok')"
if ($LASTEXITCODE -ne 0) { Fail "依赖自检失败" }

Write-Host ""
Write-Host "==== 环境安装完成 ====" -ForegroundColor Green
Write-Host "下一步（必须手工做一次）：打开并登录微信 4.0（Weixin.exe），"
Write-Host "然后右键 setup-keys.cmd → 以管理员身份运行。"
Write-Host "日常命令：  ppchat.cmd -c `"from ppchat import parse; print(parse.ingest())`""
Write-Host "说明文档：  docs\windows-install.md"
exit 0
