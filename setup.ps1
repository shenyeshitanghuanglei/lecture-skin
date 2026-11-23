# 新手引导与一键安装：自动检查并安装 uv → 自动检查并安装 Python 3.12 与依赖 → 协助安装虚拟声卡 VB-CABLE
# 用法：双击「一键安装.bat」或首次双击「声音面板.bat」，或在 PowerShell 里运行  .\setup.ps1
# 需要代理时先设置 HTTPS_PROXY，uv 会自动使用。
param(
    [switch]$NoOpen,    # 不自动打开浏览器 / 不进入交互等待（测试用）
    [switch]$NoLaunch   # 安装完成后不询问启动声音面板（供 声音面板.bat 调用）
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

$VbCableUrl = 'https://vb-audio.com/Cable/'

function Write-Banner([string]$text) {
    Write-Host "`n============================================================" -ForegroundColor Magenta
    Write-Host "  $text" -ForegroundColor White
    Write-Host "============================================================" -ForegroundColor Magenta
}
function Write-Step([string]$text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Write-Ok([string]$text) { Write-Host "    [OK] $text" -ForegroundColor Green }
function Write-Warn([string]$text) { Write-Host "    [!]  $text" -ForegroundColor Yellow }

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in @(
        (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
        (Join-Path $env:USERPROFILE '.cargo\bin\uv.exe')
    )) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Test-VbCable {
    $cable = Get-CimInstance Win32_SoundDevice -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like '*VB-Audio Virtual Cable*' -and $_.Status -eq 'OK' }
    return [bool]$cable
}

Write-Banner 'lecture-skin · 新手引导与环境准备'
Write-Host '  将自动完成：1) 检查/安装 uv  2) 检查/安装 Python 3.12 与依赖  3) 协助安装虚拟声卡' -ForegroundColor Gray

# ---------- 1. 自动检查并安装 uv ----------
Write-Step '步骤 1/3：自动检查 uv'
$uv = Find-Uv
if (-not $uv) {
    Write-Warn '未检测到 uv，正在从官方地址自动安装（https://astral.sh/uv）…'
    powershell -NoProfile -ExecutionPolicy ByPass -Command 'irm https://astral.sh/uv/install.ps1 | iex'
    if ($LASTEXITCODE -ne 0) { throw 'uv 自动安装失败，请检查网络或设置 HTTPS_PROXY 后重试' }
    $localBin = Join-Path $env:USERPROFILE '.local\bin'
    $cargoBin = Join-Path $env:USERPROFILE '.cargo\bin'
    $env:Path = "$localBin;$cargoBin;$env:Path"
    $uv = Find-Uv
    if (-not $uv) { throw '已安装 uv，但当前窗口未找到 uv.exe；请关闭并重新运行本脚本' }
}
$uvVer = (& $uv --version 2>$null)
Write-Ok "uv 已就绪：$uvVer ($uv)"

# ---------- 2. 自动检查并安装 Python 3.12 + 项目依赖 ----------
Write-Step '步骤 2/3：自动检查 Python 3.12 与项目依赖'
$py312 = $null
try {
    $foundPy = (& $uv python find 3.12 2>$null)
    if ($LASTEXITCODE -eq 0 -and $foundPy) {
        $py312 = ($foundPy | Select-Object -First 1).ToString().Trim()
    }
} catch {
    $py312 = $null
}

if (-not $py312) {
    Write-Warn '未检测到 Python 3.12，正在通过 uv 自动下载安装 Python 3.12…'
    & $uv python install 3.12
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 自动安装失败，请检查网络或代理设置后重试' }
    $py312 = ((& $uv python find 3.12 2>$null) | Select-Object -First 1).ToString().Trim()
}
Write-Ok "Python 3.12 已就绪：$py312"

Write-Host '    正在同步项目虚拟环境与依赖（首次约需下载 3 GB，请耐心等待）…'
& $uv sync --locked
if ($LASTEXITCODE -ne 0) { throw 'uv sync --locked 失败，请查看上方报错。下载失败时检查网络和代理设置。' }
$venvPy = Join-Path $Root '.venv\Scripts\python.exe'
$venvVer = if (Test-Path $venvPy) { (& $venvPy --version 2>$null) } else { 'Python 3.12' }
Write-Ok "项目环境已就绪：$venvVer (.venv)"

# ---------- 3. 协助安装虚拟声卡 VB-CABLE ----------
Write-Step '步骤 3/3：检查并协助安装虚拟声卡（VB-CABLE）'
$cableOk = Test-VbCable
if ($cableOk) {
    Write-Ok '已检测到 VB-Audio Virtual Cable，虚拟声卡就绪'
} else {
    Write-Warn '未检测到虚拟声卡 VB-CABLE（用于把浏览器网课声音送入变声器）。'
    Write-Host "    官方下载链接：$VbCableUrl" -ForegroundColor White
    Write-Host '    安装步骤：'
    Write-Host '      1) 在官网下载 VB-CABLE 压缩包并完整解压'
    Write-Host '      2) 右键以「管理员身份」运行 VBCABLE_Setup_x64.exe 安装'
    Write-Host '      3) 安装完成后建议重启 Windows（若不想现在重启，也可稍后在声音面板里继续）'
    if (-not $NoOpen) {
        Start-Process $VbCableUrl
        Write-Host '    已为你打开 VB-CABLE 官方下载页面。' -ForegroundColor Cyan
        while (-not $cableOk) {
            $ans = Read-Host '    安装完成后按 [回车] 重新检测，或输入 s 跳过（稍后在面板新手引导中继续）'
            if ($ans -match '^[sS]$') {
                Write-Warn '已跳过虚拟声卡复检，你可在打开声音面板后于「新手引导」里随时重新检测。'
                break
            }
            $cableOk = Test-VbCable
            if ($cableOk) {
                Write-Ok '检测成功！VB-Audio Virtual Cable 已就绪'
            } else {
                Write-Warn '暂未检测到 VB-CABLE（部分系统需重启 Windows 后生效）。可重试或输入 s 跳过。'
            }
        }
    }
}

# ---------- 4. 简单功能教学摘要 ----------
Write-Step '快速上手（3 步开始变声）'
Write-Host '    1. 打开声音面板（首次进入会自动弹出图形化「新手引导」）'
Write-Host '    2. 在「声音库」导入你的 RVC 模型（.pth，可选 .index），点击「使用」'
Write-Host '    3. 在 Windows 声音设置中，把浏览器输出设为「CABLE Input」（耳机/音箱保持为实际播放设备），回主页点「开始变声」'

if (-not $cableOk) {
    Write-Warn "提醒：使用前请先完成 VB-CABLE 安装（$VbCableUrl）并重启电脑。"
}

if (-not $NoOpen -and -not $NoLaunch) {
    $pythonw = Join-Path $Root '.venv\Scripts\pythonw.exe'
    if (Test-Path $pythonw) {
        $go = Read-Host "`n现在打开「声音面板」吗？直接回车打开，输入 n 退出 [Y/n]"
        if ($go -notmatch '^[nN]$') {
            $desktopScript = Join-Path $Root 'app\desktop.py'
            Start-Process -FilePath $pythonw -ArgumentList ('"' + $desktopScript + '"')
            Write-Ok '已启动「声音面板」'
        }
    }
} else {
    Write-Host "`n环境准备完成。双击「声音面板.bat」即可打开面板。" -ForegroundColor Green
}
