# Dual-host macro MBO download: local shard 0 + CHI404 shard 1.
param(
    [int]$Workers = 32,
    [int]$ShardCount = 2,
    [int]$LocalShard = 0,
    [int]$RemoteShard = 1,
    [switch]$PriorityEvents = $true,
    [switch]$FullUniverse
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path $PSScriptRoot -Parent
Set-Location $Repo

$PriorityFlag = @()
if ($PriorityEvents -and -not $FullUniverse) {
    $PriorityFlag = @("--priority-events")
    Write-Host "Mode: priority events (Tier 1-3 + UNEMPLOYMENT_CLAIMS)"
} else {
    Write-Host "Mode: full macro_releases universe"
}

Write-Host "Stopping existing local download processes..."
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*download_mbo_release_data*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Write-Host "Syncing download stack to chi404..."
ssh chi404 "mkdir -p /root/hft3/repo/packages /root/hft3/repo/scripts /root/hft3/repo/apps /root/hft3/repo/runtime/data_downloads /root/hft3/repo/data/mbo_release /root/hft3/repo/data/npz"
scp "$Repo\hft3_bootstrap.py" chi404:/root/hft3/repo/
scp "$Repo\scripts\download_mbo_release_data.py" chi404:/root/hft3/repo/scripts/
scp -r "$Repo\packages\mbo_release_lane" "$Repo\packages\economic_event_universe" "$Repo\packages\data_system" "$Repo\packages\backtest_pipeline" "$Repo\packages\features_engine" chi404:/root/hft3/repo/packages/
scp -r "$Repo\apps\workbench" chi404:/root/hft3/repo/apps/

$envPath = Join-Path $Repo ".env"
if (-not (Test-Path $envPath)) { throw "Missing $envPath" }
$keyLine = (Get-Content $envPath -Encoding UTF8 | Where-Object { $_ -match '^DATABENTO_API_KEY=' } | Select-Object -First 1)
if (-not $keyLine) { throw "DATABENTO_API_KEY not found in .env" }
ssh chi404 "touch /root/hft3/.env && chmod 600 /root/hft3/.env && grep -v '^DATABENTO_API_KEY=' /root/hft3/.env > /root/hft3/.env.tmp || true; mv /root/hft3/.env.tmp /root/hft3/.env; echo '$keyLine' >> /root/hft3/.env; chmod 600 /root/hft3/.env"

Write-Host "Starting CHI404 shard $RemoteShard/$ShardCount workers=$Workers..."
$remoteStart = "pkill -f download_mbo_release_data.py 2>/dev/null || true; cd /root/hft3/repo && set -a && . /root/hft3/.env && set +a && nohup python3 scripts/download_mbo_release_data.py --download --derive-npz --scope macro_releases $($PriorityFlag -join ' ') --workers $Workers --shard-index $RemoteShard --shard-count $ShardCount --output runtime/data_downloads/mbo_download_report_chi404.json >> runtime/data_downloads/macro_releases_chi404.log 2>&1 & sleep 1 && pgrep -af download_mbo_release_data.py"
ssh chi404 $remoteStart

Write-Host "Starting local shard $LocalShard/$ShardCount workers=$Workers..."
Start-Process -NoNewWindow -FilePath "python" -ArgumentList @(
    "scripts/download_mbo_release_data.py",
    "--download",
    "--derive-npz",
    "--scope", "macro_releases"
) + $PriorityFlag + @(
    "--workers", "$Workers",
    "--shard-index", "$LocalShard",
    "--shard-count", "$ShardCount",
    "--output", "runtime/data_downloads/mbo_download_report_local.json"
) -RedirectStandardOutput "runtime/data_downloads/macro_releases_local.log" -RedirectStandardError "runtime/data_downloads/macro_releases_local.err.log"

Write-Host "Dual download running."
Write-Host "  Local log:  runtime/data_downloads/macro_releases_local.log"
Write-Host "  CHI404 log: ssh chi404 tail -f /root/hft3/repo/runtime/data_downloads/macro_releases_chi404.log"
Write-Host "  Merge later: scp -r chi404:/root/hft3/repo/data/mbo_release/* data/mbo_release/"
