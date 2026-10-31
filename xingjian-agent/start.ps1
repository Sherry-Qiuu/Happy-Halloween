# Start the Xingjian agent service on Windows.
#   .\start.ps1              # http://127.0.0.1:8000
#   .\start.ps1 -Port 8080   # another port
#   .\start.ps1 -Mock        # offline demo, no API key needed
param(
    [int]$Port = 0,
    [switch]$Mock
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $python) {
    Write-Host 'Python 3.9+ is required: https://www.python.org/downloads/' -ForegroundColor Red
    exit 1
}

if ($Port -gt 0) { $env:PORT = "$Port" }
if ($Mock) { $env:AGENT_MOCK = '1' }

if (-not (Test-Path '.env') -and -not $env:DEEPSEEK_API_KEY) {
    Write-Host 'No .env found. Copy .env.example to .env and add DEEPSEEK_API_KEY,' -ForegroundColor Yellow
    Write-Host 'or run: .\start.ps1 -Mock   (offline demo)' -ForegroundColor Yellow
}

& $python.Source server.py
exit $LASTEXITCODE
