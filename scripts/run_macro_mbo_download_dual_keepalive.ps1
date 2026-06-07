# Dual-host priority MBO download with keepalive on BOTH machines.
param(
    [int]$Workers = 32,
    [int]$ShardCount = 2,
    [int]$LocalShard = 0,
    [int]$RemoteShard = 1
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path $PSScriptRoot -Parent
Set-Location $Repo

Write-Host "Mode: priority events (Tier 1-3 + UNEMPLOYMENT_CLAIMS) - dual keepalive"

Write-Host "Stopping existing local download processes..."
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*download_mbo_release_data*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Write-Host "Syncing download stack to chi404..."
ssh chi404 "mkdir -p /root/hft3/repo/packages /root/hft3/repo/scripts /root/hft3/repo/apps /root/hft3/repo/runtime/data_downloads"
scp "$Repo\hft3_bootstrap.py" chi404:/root/hft3/repo/
scp "$Repo\scripts\download_mbo_release_data.py" chi404:/root/hft3/repo/scripts/
scp "$Repo\scripts\run_chi404_mbo_download_keepalive.sh" chi404:/root/hft3/repo/scripts/
scp -r "$Repo\packages\mbo_release_lane" "$Repo\packages\economic_event_universe" "$Repo\packages\data_system" "$Repo\packages\backtest_pipeline" "$Repo\packages\features_engine" chi404:/root/hft3/repo/packages/
scp -r "$Repo\apps\workbench" chi404:/root/hft3/repo/apps/

$envPath = Join-Path $Repo ".env"
if (-not (Test-Path $envPath)) { throw "Missing $envPath" }
$keyLine = (Get-Content $envPath -Encoding UTF8 | Where-Object { $_ -match '^DATABENTO_API_KEY=' } | Select-Object -First 1)
if (-not $keyLine) { throw "DATABENTO_API_KEY not found in .env" }
ssh chi404 "touch /root/hft3/.env && chmod 600 /root/hft3/.env && grep -v '^DATABENTO_API_KEY=' /root/hft3/.env > /root/hft3/.env.tmp || true; mv /root/hft3/.env.tmp /root/hft3/.env; echo '$keyLine' >> /root/hft3/.env; chmod 600 /root/hft3/.env"

Write-Host "Stopping existing CHI404 download processes..."
ssh chi404 "pkill -f run_chi404_mbo_download_keepalive.sh 2>/dev/null || true; pkill -f 'python3 scripts/download_mbo_release_data' 2>/dev/null || true; sleep 1"

Write-Host "Starting CHI404 keepalive shard $RemoteShard/$ShardCount workers=$Workers..."
ssh chi404 "sed -i 's/\r$//' /root/hft3/repo/scripts/run_chi404_mbo_download_keepalive.sh 2>/dev/null || true; chmod +x /root/hft3/repo/scripts/run_chi404_mbo_download_keepalive.sh; WORKERS=$Workers SHARD_INDEX=$RemoteShard SHARD_COUNT=$ShardCount nohup bash /root/hft3/repo/scripts/run_chi404_mbo_download_keepalive.sh >> /root/hft3/repo/runtime/data_downloads/chi404_keepalive.log 2>&1 & sleep 2; pgrep -af run_chi404_mbo_download_keepalive"


Write-Host "Stopping existing local keepalive processes..."
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*run_local_mbo_keepalive.ps1*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Host "Starting local keepalive shard $LocalShard/$ShardCount workers=$Workers..."
$localScriptPath = Join-Path $Repo "runtime\data_downloads\run_local_mbo_keepalive.ps1"
@(
    "`$Repo = '$Repo'"
    "Set-Location `$Repo"
    "while (`$true) {"
    "  Write-Host ""[keepalive] local shard $LocalShard/$ShardCount workers=$Workers"""
    "  python scripts/download_mbo_release_data.py --download --derive-npz --scope macro_releases --priority-events --workers $Workers --shard-index $LocalShard --shard-count $ShardCount --output runtime/data_downloads/mbo_download_report_local.json 2>&1 | Tee-Object -FilePath runtime/data_downloads/macro_releases_local.log -Append"
    "  Start-Sleep -Seconds 30"
    "}"
) | Set-Content -Path $localScriptPath -Encoding UTF8
Start-Process -NoNewWindow powershell -ArgumentList @("-NoProfile", "-File", $localScriptPath)

Write-Host "Dual keepalive running on BOTH hosts."
Write-Host "  Local log:  runtime/data_downloads/macro_releases_local.log"
Write-Host "  CHI404 log: ssh chi404 tail -f /root/hft3/repo/runtime/data_downloads/macro_releases_chi404.log"
