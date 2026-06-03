# Full semantic graph rebuild via local Ollama (no Google/Gemini API).
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot 'logs\graphify'
$LogFile = Join-Path $LogDir 'semantic_local.log'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-LogLine {
    param([string]$Message)
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$ts] $Message"
    $line | Tee-Object -FilePath $LogFile -Append
}

if (-not (Get-Command graphify -ErrorAction SilentlyContinue)) {
    Write-Error 'graphify not on PATH (pip install graphifyy)'
    exit 1
}

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Error 'ollama not on PATH'
    exit 1
}

$Model = if ($env:GRAPHIFY_OLLAMA_MODEL) { $env:GRAPHIFY_OLLAMA_MODEL } else { 'gemma4:31b-cloud' }
$HostUrl = if ($env:OLLAMA_HOST) { $env:OLLAMA_HOST } else { 'http://127.0.0.1:11434' }
$Timeout = if ($env:GRAPHIFY_OLLAMA_TIMEOUT_S) { $env:GRAPHIFY_OLLAMA_TIMEOUT_S } else { '600' }

# graphify ollama backend uses OpenAI-compatible client against Ollama /v1
if (-not $env:OLLAMA_API_KEY) {
    $env:OLLAMA_API_KEY = 'local'
}

Write-LogLine "graphify semantic local start model=$Model host=$HostUrl"
Write-LogLine 'Requires: pip install openai graphifyy'

try {
    python -c "import openai" 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'missing openai package — run: pip install openai' }
} catch {
    Write-LogLine "ERROR: $_"
    exit 1
}

& graphify extract . `
    --backend ollama `
    --model $Model `
    --max-concurrency 1 `
    --api-timeout $Timeout `
    --out . 2>&1 | Tee-Object -FilePath $LogFile -Append

if ($LASTEXITCODE -ne 0) {
    Write-LogLine "graphify extract failed (exit $LASTEXITCODE)"
    exit $LASTEXITCODE
}

Write-LogLine 'graphify semantic local done'
exit 0
