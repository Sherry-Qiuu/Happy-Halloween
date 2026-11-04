# Store a DeepSeek API key in .env without echoing it, then optionally
# restart the local service so the AI panel goes live.
#
#   .\set-key.ps1                  # only write the key
#   .\set-key.ps1 -Restart         # write + restart the app on :8000
#
# The key is written to .env, which .gitignore excludes, so it never
# reaches GitHub and is never sent to the browser: only the server reads
# it and proxies the conversation.
param(
    [string]$Key = "",
    [switch]$Restart
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$envFile = Join-Path $PSScriptRoot '.env'
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $PSScriptRoot '.env.example') $envFile
    Write-Host '.env 不存在，已从 .env.example 创建' -ForegroundColor Yellow
}

# --- 1. read the key without echoing it -------------------------------
if (-not $Key) {
    $secure = Read-Host '粘贴 DeepSeek API key（输入不会显示）' -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $Key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

$Key = $Key.Trim()
if (-not $Key) {
    Write-Host '没有输入内容，已取消。' -ForegroundColor Red
    exit 1
}
if ($Key -notmatch '^sk-[A-Za-z0-9]{16,}$') {
    Write-Host '提示：DeepSeek 的 key 一般是 sk- 开头的 35 位字符串，确认没粘错。' -ForegroundColor Yellow
}

# --- 2. verify the key against the API (network errors are tolerated) --
$keyOk = $null
$checker = Join-Path $PSScriptRoot 'scripts\check_key.py'
if ((Get-Command python -ErrorAction SilentlyContinue) -and (Test-Path $checker)) {
    $env:XJ_KEY_CHECK = $Key
    try {
        $verdict = (& python $checker 2>$null | Select-Object -First 1)
    }
    finally {
        Remove-Item Env:\XJ_KEY_CHECK -ErrorAction SilentlyContinue
    }
    if ($verdict -like 'valid*') {
        $keyOk = $true
        Write-Host "key 校验通过：$($verdict.Substring(6))" -ForegroundColor Green
    }
    elseif ($verdict -like 'rejected*') {
        $keyOk = $false
        Write-Host "key 被拒绝（HTTP $($verdict.Substring(9))），先不要写进配置。" -ForegroundColor Red
        exit 1
    }
    else {
        Write-Host '无法在线校验（网络问题），仍然写入，稍后用真实对话验证。' -ForegroundColor Yellow
    }
}

# --- 3. write it into .env -------------------------------------------
$lines = Get-Content -LiteralPath $envFile
$found = $false
$updated = foreach ($line in $lines) {
    if ($line -match '^\s*DEEPSEEK_API_KEY\s*=') {
        $found = $true
        "DEEPSEEK_API_KEY=$Key"
    }
    else {
        $line
    }
}
if (-not $found) {
    $updated = @($updated) + "DEEPSEEK_API_KEY=$Key"
}
Set-Content -LiteralPath $envFile -Value $updated -Encoding UTF8

$masked = $Key.Substring(0, 6) + ('*' * [Math]::Max(0, $Key.Length - 10)) + $Key.Substring($Key.Length - 4)
Write-Host ''
Write-Host "已写入 $envFile" -ForegroundColor Green
Write-Host "  DEEPSEEK_API_KEY=$masked  （长度 $($Key.Length)）"
Write-Host '  .gitignore 已排除 .env，不会进 GitHub。'

if (-not $Restart) {
    Write-Host ''
    Write-Host '重启服务后生效：' -ForegroundColor Cyan
    Write-Host '  .\start-local.ps1'
    Write-Host '或者重新运行：.\set-key.ps1 -Restart' -ForegroundColor Cyan
    exit 0
}

# --- 4. restart the app on :8000 -------------------------------------
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { throw 'Python not found on PATH' }

# Get-NetTCPConnection is blocked in some sandboxes, so fall back to netstat.
$ownerIds = @()
try {
    $ownerIds = @(
        (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction Stop).OwningProcess
    )
}
catch {
    $rows = netstat -ano | Select-String 'LISTENING' | Select-String ':8000\s'
    foreach ($row in $rows) {
        $ownerIds += ($row.ToString().Trim() -split '\s+')[-1]
    }
}

foreach ($ownerId in ($ownerIds | Select-Object -Unique)) {
    if (-not $ownerId) { continue }
    $proc = Get-Process -Id $ownerId -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -match 'python') {
        Write-Host "停止旧的 python 进程 (pid $($proc.Id))" -ForegroundColor Yellow
        Stop-Process -Id $proc.Id -Force
        Start-Sleep -Milliseconds 800
    }
}

Start-Process -FilePath $python -ArgumentList 'server.py' `
    -WorkingDirectory $PSScriptRoot -WindowStyle Minimized
Write-Host '正在启动服务…' -ForegroundColor Cyan

$health = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/agent/health' -TimeoutSec 3
        break
    }
    catch {
        continue
    }
}

if (-not $health) {
    Write-Host '服务没有起来，检查端口 8000 是否被占用。' -ForegroundColor Red
    exit 1
}

if ($health.configured) {
    Write-Host ''
    Write-Host "AI 已接上：model=$($health.model)  configured=true" -ForegroundColor Green
    Write-Host '刷新页面，AI 面板右上角会变成绿点，回复来自真实 DeepSeek。'
}
else {
    Write-Host '服务起来了，但 configured=false：确认 .env 里 DEEPSEEK_API_KEY 已写入。' -ForegroundColor Yellow
}
