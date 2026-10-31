# Start the app and a public Cloudflare quick tunnel.
#
#   .\start-local.ps1                 # app on :8000 + public https URL
#   .\start-local.ps1 -Port 8080
#   .\start-local.ps1 -NoTunnel       # local only
#
# Prints the https://<random>.trycloudflare.com address. That address
# works from any network without a public IP or port forwarding, and it
# changes every time you restart. For a fixed calhub.asia address use a
# named tunnel (see deploy/DNS.md).
param(
    [int]$Port = 8000,
    [switch]$NoTunnel
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    $py = (Get-Command py -ErrorAction SilentlyContinue).Source
    if ($py) { $python = $py } else { throw 'Python 3.9+ not found on PATH' }
}

$envFile = Join-Path $PSScriptRoot '.env'
$hasKey = $env:DEEPSEEK_API_KEY
if (-not $hasKey -and (Test-Path $envFile)) {
    $line = Select-String -Path $envFile -Pattern '^DEEPSEEK_API_KEY=(.+)$' |
        Select-Object -First 1
    if ($line) { $hasKey = $line.Matches[0].Groups[1].Value.Trim() }
}
if (-not $hasKey) {
    Write-Host 'DEEPSEEK_API_KEY is empty: the AI panel runs in offline demo mode.' -ForegroundColor Yellow
}

$server = Start-Process -FilePath $python -ArgumentList 'server.py' `
    -WorkingDirectory $PSScriptRoot -PassThru -WindowStyle Minimized
Write-Host "app    : http://127.0.0.1:$Port  (pid $($server.Id))"

Start-Sleep -Seconds 2
try {
    if ($NoTunnel) {
        Write-Host 'tunnel : disabled (-NoTunnel)'
        Read-Host 'Press Enter to stop'
        exit 0
    }

    $cf = Join-Path $PSScriptRoot 'tools\cloudflared.exe'
    if (-not (Test-Path $cf)) {
        throw "cloudflared is missing: $cf"
    }
    Write-Host 'tunnel : starting, the public URL appears below in a few seconds'
    & $cf tunnel --url "http://127.0.0.1:$Port" --no-autoupdate --edge-ip-version 4
}
finally {
    if ($server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
        Write-Host 'app stopped'
    }
}
