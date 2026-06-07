# Load hft3 research credentials from QuantX desk keyring (no secrets printed).
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)

function Import-PlainEnvFile {
    param([string]$Path, [switch]$Override)
    if (-not $Path -or -not (Test-Path $Path)) { return $false }
    Get-Content $Path | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $name = $matches[1].Trim()
            $val = $matches[2].Trim().Trim('"').Trim("'")
            if ($Override -or -not (Get-Item -Path "Env:$name" -ErrorAction SilentlyContinue)) {
                Set-Item -Path "Env:$name" -Value $val
            }
        }
    }
    return $true
}

$Parent = Split-Path -Parent $RepoRoot
foreach ($sibling in @("quant-x", "crypto-alpha-engine")) {
    $sibEnv = Join-Path (Join-Path $Parent $sibling) ".env"
    if (Test-Path $sibEnv) {
        Import-PlainEnvFile -Path $sibEnv | Out-Null
    }
}

$keysFile = $null
foreach ($envName in @("CRYPTO_KEYS_ENV", "MACRO_KEYS_ENV", "QXL_KEYS_ENV")) {
    $candidate = (Get-Item -Path "Env:$envName" -ErrorAction SilentlyContinue).Value
    if ($candidate -and (Test-Path $candidate)) {
        $keysFile = $candidate
        break
    }
}
if (-not $keysFile -and (Test-Path "C:\QuantX\keys.env")) {
    $keysFile = "C:\QuantX\keys.env"
}
if (-not $keysFile) {
    $desk = Join-Path $env:USERPROFILE "Desktop\keys.env"
    if (Test-Path $desk) { $keysFile = $desk }
}

if ($keysFile) {
    Import-PlainEnvFile -Path $keysFile | Out-Null
    if (-not $env:QXL_KEYS_ENV) { $env:QXL_KEYS_ENV = $keysFile }
}

# Alias QuantX B2 names to hft3 crypto lane (matches desk_env / env_loader).
if (-not $env:HFT3_CRYPTO_B2_KEY_ID -and $env:AWS_ACCESS_KEY_ID) {
    $env:HFT3_CRYPTO_B2_KEY_ID = $env:AWS_ACCESS_KEY_ID
}
if (-not $env:HFT3_CRYPTO_B2_APP_KEY -and $env:AWS_SECRET_ACCESS_KEY) {
    $env:HFT3_CRYPTO_B2_APP_KEY = $env:AWS_SECRET_ACCESS_KEY
}
if (-not $env:HFT3_CRYPTO_B2_SOURCE_BUCKET -and $env:B2_BUCKET) {
    $env:HFT3_CRYPTO_B2_SOURCE_BUCKET = $env:B2_BUCKET
}
if (-not $env:HFT3_CRYPTO_B2_ENDPOINT) {
    if ($env:CAE_B2_ENDPOINT) { $env:HFT3_CRYPTO_B2_ENDPOINT = $env:CAE_B2_ENDPOINT }
    elseif ($env:B2_ENDPOINT_URL) { $env:HFT3_CRYPTO_B2_ENDPOINT = $env:B2_ENDPOINT_URL }
}
