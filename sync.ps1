# 本地拉新数据 → 同步到云服务器 → 重启服务
#
# 用法：
#   .\sync.ps1                        # 用 sync.config.json 里的服务器信息
#   .\sync.ps1 -ServerIP <IP>         # 临时覆盖 IP
#   .\sync.ps1 -SkipFetch             # 跳过本地拉取，仅同步现有缓存
#   .\sync.ps1 -DryRun                # 不真的传文件，看会做什么
#
# 首次使用前：把 sync.config.example.json 复制为 sync.config.json 并填好

param(
    [string]$ServerIP = "",
    [string]$ServerUser = "root",
    [string]$ServerPath = "/root/dota-stats",
    [string]$ServiceName = "dota-stats",
    [string]$IdentityFile = "",       # 可选，SSH 私钥路径；不填用密码
    [switch]$SkipFetch,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

# ---- 读 sync.config.json ----
$configPath = Join-Path $scriptDir "sync.config.json"
if (Test-Path $configPath) {
    $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
    if (-not $ServerIP -and $cfg.server_ip)    { $ServerIP = $cfg.server_ip }
    if ($cfg.server_user)                       { $ServerUser = $cfg.server_user }
    if ($cfg.server_path)                       { $ServerPath = $cfg.server_path }
    if ($cfg.service_name)                      { $ServiceName = $cfg.service_name }
    if ($cfg.identity_file)                     { $IdentityFile = $cfg.identity_file }
}

if (-not $ServerIP) {
    Write-Host "❌ 错误：没有指定 ServerIP。" -ForegroundColor Red
    Write-Host "   把 sync.config.example.json 复制为 sync.config.json 填入你的服务器 IP，"
    Write-Host "   或用 .\sync.ps1 -ServerIP <你的IP>"
    exit 2
}

# ---- 显示计划 ----
Write-Host ""
Write-Host "🎯 同步计划" -ForegroundColor Cyan
Write-Host "   服务器:    $ServerUser@$ServerIP : $ServerPath"
Write-Host "   服务名:    $ServiceName"
Write-Host "   本地拉取:  $(if ($SkipFetch) {'跳过'} else {'是 (python -m src.main --no-export)'})"
Write-Host "   Dry run:   $DryRun"
Write-Host ""

# ---- SSH 参数 ----
$sshArgs = @()
$scpArgs = @()
if ($IdentityFile) {
    $sshArgs += "-i", $IdentityFile
    $scpArgs += "-i", $IdentityFile
}

# ---- 步骤 1: 本地拉取 ----
if (-not $SkipFetch) {
    Write-Host "[1/3] 本地拉取最新数据（这一步访问 Steam + OpenDota）…" -ForegroundColor Yellow
    $env:PYTHONIOENCODING = "utf-8"
    if ($DryRun) {
        Write-Host "   [DRY] python -m src.main --no-export" -ForegroundColor DarkGray
    } else {
        python -m src.main --no-export
        if ($LASTEXITCODE -ne 0) {
            Write-Host "❌ 本地拉取失败，停止同步。" -ForegroundColor Red
            exit 1
        }
    }
} else {
    Write-Host "[1/3] 跳过本地拉取" -ForegroundColor DarkGray
}

# ---- 步骤 2: 同步 data/ 到服务器 ----
Write-Host ""
Write-Host "[2/3] 上传 data/ 到服务器…" -ForegroundColor Yellow

$matchCount = (Get-ChildItem -Path "data\matches" -Filter "*.json" -ErrorAction SilentlyContinue).Count
$aggregateExists = Test-Path "data\aggregate.json"
Write-Host "   本地: data/matches/ 有 $matchCount 个 .json,  aggregate.json = $aggregateExists"

if ($DryRun) {
    Write-Host "   [DRY] scp -r data ${ServerUser}@${ServerIP}:${ServerPath}/" -ForegroundColor DarkGray
} else {
    # 用 tar 打包传输，比 scp -r 一个个传快得多（500 个小文件传输能从 5min 缩到 30s）
    # 同时避免覆盖时 scp 跳着传引入的不确定性
    $tarPath = Join-Path $scriptDir "data-sync.tar.gz"
    Write-Host "   打包 data/..."
    tar.exe -czf $tarPath data 2>&1 | Out-Null

    Write-Host "   上传 $(([math]::Round((Get-Item $tarPath).Length/1MB, 1))) MB..."
    & scp @scpArgs $tarPath "${ServerUser}@${ServerIP}:${ServerPath}/data-sync.tar.gz"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ scp 失败" -ForegroundColor Red
        Remove-Item $tarPath -ErrorAction SilentlyContinue
        exit 1
    }

    Write-Host "   服务器端解压..."
    & ssh @sshArgs "${ServerUser}@${ServerIP}" "cd ${ServerPath} && tar xzf data-sync.tar.gz && rm data-sync.tar.gz"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ 远程解压失败" -ForegroundColor Red
        exit 1
    }

    Remove-Item $tarPath -ErrorAction SilentlyContinue
}

# ---- 步骤 3: 重启服务 ----
Write-Host ""
Write-Host "[3/3] 重启服务器上的 $ServiceName 服务…" -ForegroundColor Yellow
if ($DryRun) {
    Write-Host "   [DRY] ssh ${ServerUser}@${ServerIP} systemctl restart $ServiceName" -ForegroundColor DarkGray
} else {
    & ssh @sshArgs "${ServerUser}@${ServerIP}" "systemctl restart $ServiceName && sleep 1 && systemctl is-active $ServiceName"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "⚠️  服务重启可能失败，去服务器 journalctl -u $ServiceName 看看" -ForegroundColor Yellow
        exit 1
    }
}

Write-Host ""
Write-Host "✅ 同步完成！" -ForegroundColor Green
Write-Host "   浏览器打开 http://$ServerIP 看最新数据"
Write-Host ""
