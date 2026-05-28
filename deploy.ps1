# 推送本地代码 → 云服务器 → 重启服务
#
# 用法：
#   .\deploy.ps1                # 推代码 + 重启
#   .\deploy.ps1 -WithDeps      # 改了 requirements.txt 时加这个
#   .\deploy.ps1 -WithConfig    # 同时推 config.json（默认不推，避免覆盖服务器版）
#   .\deploy.ps1 -DryRun        # 看会做什么，不真传
#
# 复用 sync.config.json 里的服务器信息

param(
    [string]$ServerIP = "",
    [string]$ServerUser = "root",
    [string]$ServerPath = "/var/www/html/dota-inhouse-stats",
    [string]$ServiceName = "dota-stats",
    [string]$IdentityFile = "",
    [switch]$WithDeps,
    [switch]$WithConfig,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

# ---- 读 sync.config.json（复用同一份配置）----
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
    Write-Host "❌ 错误：没有指定 ServerIP（在 sync.config.json 里填）" -ForegroundColor Red
    exit 2
}

# ---- 显示计划 ----
$includeList = @("src/", "web/", "serve.py", "requirements.txt")
if ($WithConfig) { $includeList += "config.json" }

Write-Host ""
Write-Host "🚀 代码部署计划" -ForegroundColor Cyan
Write-Host "   服务器:    $ServerUser@$ServerIP : $ServerPath"
Write-Host "   服务名:    $ServiceName"
Write-Host "   推送内容:  $($includeList -join ', ')"
Write-Host "   装依赖:    $(if ($WithDeps) {'是 (pip install -r requirements.txt)'} else {'否'})"
Write-Host "   Dry run:   $DryRun"
Write-Host ""

# ---- SSH 参数 ----
$sshArgs = @()
$scpArgs = @()
if ($IdentityFile) {
    $sshArgs += "-i", $IdentityFile
    $scpArgs += "-i", $IdentityFile
}

# ---- 步骤 1: 打包代码 ----
Write-Host "[1/3] 打包代码…" -ForegroundColor Yellow

$tarPath = Join-Path $scriptDir "code-deploy.tar.gz"
$tarItems = @("src", "web", "serve.py", "requirements.txt")
if ($WithConfig -and (Test-Path "config.json")) { $tarItems += "config.json" }

# 检查必备文件都在
foreach ($item in $tarItems) {
    if (-not (Test-Path $item)) {
        Write-Host "❌ 找不到 $item，停止" -ForegroundColor Red
        exit 1
    }
}

if ($DryRun) {
    Write-Host "   [DRY] tar -czf code-deploy.tar.gz $($tarItems -join ' ') --exclude='__pycache__'" -ForegroundColor DarkGray
} else {
    # --exclude 排除 __pycache__；--transform 不需要，目录结构保留
    tar.exe --exclude='__pycache__' --exclude='*.pyc' -czf $tarPath $tarItems 2>&1 | Out-Null
    if (-not (Test-Path $tarPath)) {
        Write-Host "❌ 打包失败" -ForegroundColor Red
        exit 1
    }
    $sizeMB = [math]::Round((Get-Item $tarPath).Length / 1MB, 2)
    Write-Host "   包大小: $sizeMB MB"
}

# ---- 步骤 2: 上传 + 解压 ----
Write-Host ""
Write-Host "[2/3] 上传到服务器并解压…" -ForegroundColor Yellow

if ($DryRun) {
    Write-Host "   [DRY] scp code-deploy.tar.gz ${ServerUser}@${ServerIP}:${ServerPath}/" -ForegroundColor DarkGray
    Write-Host "   [DRY] ssh ... 'cd $ServerPath && tar xzf code-deploy.tar.gz && rm code-deploy.tar.gz'" -ForegroundColor DarkGray
} else {
    & scp @scpArgs $tarPath "${ServerUser}@${ServerIP}:${ServerPath}/code-deploy.tar.gz"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ scp 失败" -ForegroundColor Red
        Remove-Item $tarPath -ErrorAction SilentlyContinue
        exit 1
    }

    # 解压会覆盖 src/web/serve.py/requirements.txt，但 data/.env/.venv 保持不动
    # 加 --no-overwrite-dir 防止权限被改坏；--touch 防止 pyc 时间戳跳变
    & ssh @sshArgs "${ServerUser}@${ServerIP}" "cd ${ServerPath} && tar xzf code-deploy.tar.gz && rm code-deploy.tar.gz && find src web -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null; true"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ 远程解压失败" -ForegroundColor Red
        exit 1
    }

    Remove-Item $tarPath -ErrorAction SilentlyContinue

    # 装依赖
    if ($WithDeps) {
        Write-Host "   装/升级 Python 依赖…"
        & ssh @sshArgs "${ServerUser}@${ServerIP}" "cd ${ServerPath} && .venv/bin/pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "⚠️  pip install 报错，但继续重启服务（看下面输出排查）" -ForegroundColor Yellow
        }
    }
}

# ---- 步骤 3: 重启服务 ----
Write-Host ""
Write-Host "[3/3] 重启 $ServiceName 服务…" -ForegroundColor Yellow
if ($DryRun) {
    Write-Host "   [DRY] ssh ... systemctl restart $ServiceName" -ForegroundColor DarkGray
} else {
    & ssh @sshArgs "${ServerUser}@${ServerIP}" "systemctl restart $ServiceName && sleep 2 && systemctl is-active $ServiceName"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "⚠️  服务重启失败 / 起不来，看 journalctl -u $ServiceName" -ForegroundColor Yellow
        Write-Host "   常见原因：依赖少装（用 -WithDeps 重跑）；代码语法错；端口被占" -ForegroundColor Yellow
        exit 1
    }
}

Write-Host ""
Write-Host "✅ 部署完成！" -ForegroundColor Green
Write-Host "   浏览器看效果: http://$ServerIP"
Write-Host ""
