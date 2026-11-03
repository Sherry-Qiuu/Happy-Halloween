param(
  [Parameter(Mandatory = $true)][string]$ApiKey,
  # 不填就自动从 xingjian-agent/.env 读 DEEPSEEK_API_KEY
  [string]$DeepSeekKey = '',
  [string]$RepoUrl = 'https://github.com/Sherry-Qiuu/Happy-Halloween',
  [string]$Branch = 'morrow-demo',
  [string]$ServiceName = 'morrow-demo',
  [string]$Region = 'singapore',
  [string]$Plan = 'free',
  [string]$DeepSeekBase = 'https://ciyuanzhiyan.com/v1',
  [string]$DeepSeekModel = 'deepseek-v4.1-flash'
)

# Tears: 用 Render API 创建 Docker Web Service 并触发部署，然后等它上线。
# 用法（在仓库任意目录）：
#   .\deploy-render.ps1 -ApiKey 'rnd_xxx' -DeepSeekKey 'sk-ad...'
# 需要的 API key 在 https://dashboard.render.com/u/settings#api-keys 生成。

$ErrorActionPreference = 'Stop'

if (-not $DeepSeekKey) {
  $envFile = Join-Path (Split-Path -Parent $PSScriptRoot) '.env'
  if (-not (Test-Path $envFile)) { $envFile = Join-Path $PSScriptRoot '..\.env' }
  if (Test-Path $envFile) {
    $line = (Get-Content $envFile | Where-Object { $_ -match '^DEEPSEEK_API_KEY=' } | Select-Object -First 1)
    if ($line) { $DeepSeekKey = ($line -replace '^DEEPSEEK_API_KEY=', '').Trim() }
  }
  if (-not $DeepSeekKey) { throw '缺少 DEEPSEEK_API_KEY：请用 -DeepSeekKey 传入，或在 xingjian-agent/.env 里配好' }
  Write-Host "[i] 已从 .env 读取 DEEPSEEK_API_KEY（${($DeepSeekKey.Substring(0,6))}…）"
}

$base = 'https://api.render.com/v1'
$headers = @{
  Authorization  = "Bearer $ApiKey"
  Accept         = 'application/json'
  'Content-Type' = 'application/json'
}

function Invoke-Render {
  param([string]$Method, [string]$Path, $Body)
  $uri = "$base$Path"
  try {
    if ($null -ne $Body) {
      return Invoke-RestMethod -Uri $uri -Method $Method -Headers $headers -Body ($Body | ConvertTo-Json -Depth 12)
    }
    return Invoke-RestMethod -Uri $uri -Method $Method -Headers $headers
  } catch {
    $detail = ''
    if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $detail = $_.ErrorDetails.Message }
    throw "Render API $Method $Path 失败：$($_.Exception.Message) $detail"
  }
}

Write-Host '[1/4] 读取账号信息…'
$owners = Invoke-Render -Method GET -Path '/owners?limit=20'
if (-not $owners) { throw '没有读到 owner，请确认 API key 有效' }
$owner = $owners[0].owner
if (-not $owner) { $owner = $owners[0] }
$ownerId = $owner.id
Write-Host "      owner: $($owner.name) ($ownerId)"

Write-Host '[2/4] 创建 Docker Web Service…'
$body = @{
  type        = 'web_service'
  name        = $ServiceName
  ownerId     = $ownerId
  repo        = $RepoUrl
  branch      = $Branch
  autoDeploy  = 'yes'
  envVars     = @(
    @{ key = 'HOST'; value = '0.0.0.0' },
    @{ key = 'XINGJIAN_DATA_DIR'; value = '/data' },
    @{ key = 'COOKIE_SECURE'; value = '1' },
    @{ key = 'XINGJIAN_TRUST_PROXY'; value = '1' },
    @{ key = 'XINGJIAN_ALLOW_REGISTRATION'; value = '1' },
    @{ key = 'XINGJIAN_REQUIRE_EMAIL'; value = '0' },
    @{ key = 'XINGJIAN_DEV_BYPASS_AUTH'; value = '0' },
    @{ key = 'MAIL_TRANSPORT'; value = 'console' },
    @{ key = 'DEEPSEEK_BASE_URL'; value = $DeepSeekBase },
    @{ key = 'DEEPSEEK_MODEL'; value = $DeepSeekModel },
    @{ key = 'AGENT_MOCK'; value = '0' },
    @{ key = 'AGENT_MAX_TOKENS'; value = '2400' },
    @{ key = 'DEEPSEEK_API_KEY'; value = $DeepSeekKey }
  )
  serviceDetails = @{
    env    = 'docker'
    plan   = $Plan
    region = $Region
    healthCheckPath = '/api/health'
    envSpecificDetails = @{
      dockerfilePath = './xingjian-agent/Dockerfile'
      dockerContext  = '.'
    }
  }
}

$service = Invoke-Render -Method POST -Path '/services' -Body $body
$serviceId = $service.service.id
$serviceUrl = $service.service.serviceDetails.url
Write-Host "      service id: $serviceId"
Write-Host "      地址: $serviceUrl"

Write-Host '[3/4] 触发部署…'
$deploy = Invoke-Render -Method POST -Path "/services/$serviceId/deploys" -Body @{ clearCache = 'do_not_clear' }
$deployId = $deploy.id
Write-Host "      deploy id: $deployId"

Write-Host '[4/4] 等待上线（最多 12 分钟）…'
$deadline = (Get-Date).AddMinutes(12)
$status = ''
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Seconds 15
  try {
    $d = Invoke-Render -Method GET -Path "/services/$serviceId/deploys/$deployId"
    $status = $d.status
    Write-Host "      status: $status"
    if ($status -eq 'live') { break }
    if ($status -in @('build_failed', 'canceled', 'pre_deploy_failed')) { throw "部署失败：$status" }
  } catch {
    Write-Host "      (查询失败，继续等) $($_.Exception.Message)"
  }
}

if ($status -ne 'live') {
  Write-Host "未在 12 分钟内完成，当前状态：$status"
  Write-Host "去 https://dashboard.render.com 看日志：$serviceUrl"
  exit 1
}

Write-Host ''
Write-Host '部署成功 🎉'
Write-Host "  地址: $serviceUrl"
Write-Host "  健康检查: $serviceUrl/api/health"
Write-Host ''
Write-Host '自检：'
Write-Host '  1. 打开地址 → 注册 → 应看到 26 个彩色时间块'
Write-Host '  2. 底部对话框发「明天上午十点开会两小时，帮我排一下」'
Write-Host '  3. 15 分钟无访问会休眠，首次打开等约 1 分钟'
