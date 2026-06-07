# Single supervisor for dual-host priority MBO downloads.
# Exactly one instance via global mutex. Kills stale workers on start.
param(
    [int]$Workers = 24,
    [int]$PollSeconds = 120,
    [int]$StallMinutes = 15,
    [int]$MinProgressSlots = 3
)

$ErrorActionPreference = "Continue"
$Repo = Split-Path $PSScriptRoot -Parent
Set-Location $Repo

$Log = Join-Path $Repo "runtime/data_downloads/monitor.log"
$StatePath = Join-Path $Repo "runtime/data_downloads/monitor_state.json"
$StatusPath = Join-Path $Repo "runtime/data_downloads/monitor_status.json"
$LocalKeepalive = Join-Path $Repo "runtime/data_downloads/run_local_mbo_keepalive.ps1"
$MutexName = "Global\Hft3MboDownloadSupervisor"

function Write-MonitorLog([string]$Msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Msg"
    Add-Content -Path $Log -Value $line -Encoding UTF8
    Write-Host $line
}

function Stop-StaleSupervisorProcesses {
    $myPid = $PID
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ProcessId -ne $myPid -and
            ($_.CommandLine -like '*monitor_mbo_dual_download*' -or
             $_.CommandLine -like '*run_local_mbo_keepalive*')
        } |
        ForEach-Object {
            Write-MonitorLog "Kill stale PS $($_.ProcessId): monitor/keepalive duplicate"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    $dlPids = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*download_mbo_release_data*' } |
        Select-Object -ExpandProperty ProcessId)
    if ($dlPids.Count -gt 1) {
        $dlPids | Select-Object -Skip 1 | ForEach-Object {
            Write-MonitorLog "Kill duplicate downloader $_"
            Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
        }
    }
}

function Get-LocalDownloadPids {
    @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*download_mbo_release_data*priority*' } |
        Select-Object -ExpandProperty ProcessId)
}

function Get-LocalKeepalivePid {
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*run_local_mbo_keepalive*' } |
        Select-Object -ExpandProperty ProcessId -First 1
}

function Sync-Chi404Code {
    Write-MonitorLog "Sync code -> CHI404"
    scp "$Repo\packages\mbo_release_lane\download.py" chi404:/root/hft3/repo/packages/mbo_release_lane/download.py 2>$null | Out-Null
    scp "$Repo\packages\data_system\src\budget_manager.py" chi404:/root/hft3/repo/packages/data_system/src/budget_manager.py 2>$null | Out-Null
    scp "$Repo\packages\data_system\src\databento_client.py" chi404:/root/hft3/repo/packages/data_system/src/databento_client.py 2>$null | Out-Null
    scp "$Repo\packages\data_system\src\manifest_io.py" chi404:/root/hft3/repo/packages/data_system/src/manifest_io.py 2>$null | Out-Null
    scp "$Repo\scripts\download_mbo_release_data.py" chi404:/root/hft3/repo/scripts/download_mbo_release_data.py 2>$null | Out-Null
    scp "$Repo\scripts\run_chi404_mbo_download_keepalive.sh" chi404:/root/hft3/repo/scripts/run_chi404_mbo_download_keepalive.sh 2>$null | Out-Null
    ssh chi404 "sed -i 's/\r$//' /root/hft3/repo/scripts/run_chi404_mbo_download_keepalive.sh; chmod +x /root/hft3/repo/scripts/run_chi404_mbo_download_keepalive.sh" 2>$null | Out-Null
}

function Start-Chi404Keepalive {
    Write-MonitorLog "Start CHI404 keepalive workers=$Workers shard=1/2"
    $cmd = @"
pkill -f run_chi404_mbo_download_keepalive.sh 2>/dev/null || true
pkill -f download_mbo_release_data.py 2>/dev/null || true
sleep 1
cd /root/hft3/repo
sed -i 's/\r$//' scripts/run_chi404_mbo_download_keepalive.sh
chmod +x scripts/run_chi404_mbo_download_keepalive.sh
WORKERS=$Workers SHARD_INDEX=1 SHARD_COUNT=2 nohup bash scripts/run_chi404_mbo_download_keepalive.sh >> runtime/data_downloads/chi404_keepalive.log 2>&1 &
sleep 2
pgrep -c -f run_chi404_mbo_download_keepalive.sh || echo 0
"@
    ssh chi404 $cmd 2>$null | Out-Null
}

function Start-LocalKeepalive {
    if (Get-LocalKeepalivePid) { return }
    Write-MonitorLog "Start local keepalive workers=$Workers shard=0/2"
    $content = @"
`$Repo = '$Repo'
Set-Location `$Repo
while (`$true) {
  python scripts/download_mbo_release_data.py --download --derive-npz --scope macro_releases --priority-events --workers $Workers --shard-index 0 --shard-count 2 --output runtime/data_downloads/mbo_download_report_local.json 2>&1 | Tee-Object -FilePath runtime/data_downloads/macro_releases_local.log -Append
  Start-Sleep -Seconds 30
}
"@
    Set-Content -Path $LocalKeepalive -Value $content -Encoding UTF8
    Start-Process -WindowStyle Hidden powershell -ArgumentList @("-NoProfile", "-File", $LocalKeepalive)
}

function Get-Progress {
    $dest = "$Repo\runtime\data_downloads\chi404_manifest.parquet"
    $tmp = "$dest.$PID.tmp"
    if (Test-Path $tmp) { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    scp chi404:/root/hft3/repo/data/manifest.parquet $tmp 2>$null | Out-Null
    if (Test-Path $tmp) {
        $ok = python -c "import pandas as pd; pd.read_parquet(r'$tmp'); print('ok')" 2>$null
        if ($ok -eq "ok") {
            Move-Item -Force $tmp $dest
        } else {
            Remove-Item $tmp -Force -ErrorAction SilentlyContinue
            Write-MonitorLog "WARN: skipped corrupt chi404 manifest fetch"
        }
    }
    $out = python "$Repo\scripts\mbo_monitor_progress.py" 2>$null
    if ($out) { return $out | ConvertFrom-Json }
    return $null
}

function Test-Chi404Alive {
    $raw = ssh chi404 "pgrep -c -f run_chi404_mbo_download_keepalive.sh 2>/dev/null || echo 0; pgrep -c -f 'python3 scripts/download_mbo_release_data' 2>/dev/null || echo 0" 2>$null
    if (-not $raw) { return $false }
    $parts = @($raw -split "`n" | ForEach-Object { [int]$_.Trim() })
    if ($parts[0] -gt 1) {
        Write-MonitorLog "WARN: CHI404 has $($parts[0]) keepalives - resetting"
        Start-Chi404Keepalive
    }
    return ($parts[0] -ge 1) -or ($parts[1] -ge 1)
}

# --- singleton lock ---
$mutex = New-Object System.Threading.Mutex($false, $MutexName)
$locked = $false
try {
    $locked = $mutex.WaitOne(0, $false)
} catch {
    $locked = $false
}
if (-not $locked) {
    Write-MonitorLog "Supervisor already running (mutex held) - exit"
    exit 0
}

New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null
Stop-StaleSupervisorProcesses
Write-MonitorLog "=== SUPERVISOR START pid=$PID poll=${PollSeconds}s stall=${StallMinutes}m workers=$Workers ==="

Sync-Chi404Code
Start-LocalKeepalive
Start-Chi404Keepalive

$state = @{
    last_billed_in_scope = 0
    last_progress_utc    = (Get-Date).ToUniversalTime().ToString("o")
    restarts             = 0
}
if (Test-Path $StatePath) {
    try {
        $loaded = Get-Content $StatePath -Raw | ConvertFrom-Json
        $state = @{
            last_billed_in_scope = [int]$loaded.last_billed_in_scope
            last_progress_utc    = [string]$loaded.last_progress_utc
            restarts             = [int]$loaded.restarts
        }
    } catch { }
}

$p0 = Get-Progress
if ($p0) {
    $state.last_billed_in_scope = [int]$p0.billed_in_scope
    $state.last_progress_utc = (Get-Date).ToUniversalTime().ToString("o")
    Write-MonitorLog "BASELINE: $($p0.billed_in_scope)/$($p0.total_slots) ($($p0.pct)%) spent=`$$($p0.spent_usd) rem=$($p0.remaining)"
}

try {
    while ($true) {
        $progress = Get-Progress
        $localPids = Get-LocalDownloadPids
        $chiAlive = Test-Chi404Alive
        $keepAlive = Get-LocalKeepalivePid

        $billed = if ($progress) { [int]$progress.billed_in_scope } else { 0 }
        $remaining = if ($progress) { [int]$progress.remaining } else { -1 }
        $pct = if ($progress) { [double]$progress.pct } else { 0 }
        $spent = if ($progress) { [double]$progress.spent_usd } else { 0 }
        $total = if ($progress) { [int]$progress.total_slots } else { 0 }

        $lastBilled = [int]$state.last_billed_in_scope
        # Ignore sudden large drops (corrupt manifest read).
        if ($progress -and $billed -lt ($lastBilled - 50)) {
            Write-MonitorLog "WARN: billed drop $lastBilled -> $billed ignored (bad read)"
            $billed = $lastBilled
        }
        $madeProgress = ($billed - $lastBilled) -ge $MinProgressSlots
        $lastProg = [datetime]::Parse($state.last_progress_utc)
        $stallMin = [math]::Max(0, ((Get-Date).ToUniversalTime() - $lastProg).TotalMinutes)

        if ($madeProgress) {
            $state.last_billed_in_scope = $billed
            $state.last_progress_utc = (Get-Date).ToUniversalTime().ToString("o")
            Write-MonitorLog "OK $billed/$total ($pct%) `$$spent rem=$remaining | local=$($localPids.Count) chi=$chiAlive"
        } else {
            Write-MonitorLog "CHECK $billed/$total ($pct%) stall=${stallMin}m | local=$($localPids.Count) ka=$([bool]$keepAlive) chi=$chiAlive rem=$remaining"
        }

        $needsRestart = $false
        $reason = @()

        if ($remaining -gt 0) {
            if (-not $keepAlive) { $needsRestart = $true; $reason += "no_keepalive" }
            if ($localPids.Count -eq 0) { $needsRestart = $true; $reason += "no_downloader" }
            if ($localPids.Count -gt 1) {
                $keep = $localPids[0]
                $localPids | Select-Object -Skip 1 | ForEach-Object {
                    Write-MonitorLog "Kill duplicate downloader $_"
                    Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
                }
                $localPids = @($keep)
            }
            if (-not $chiAlive) { $needsRestart = $true; $reason += "chi404_down" }
            if ($stallMin -ge $StallMinutes) { $needsRestart = $true; $reason += "stalled" }
        }

        if ($needsRestart -and $remaining -gt 0) {
            $state.restarts = [int]$state.restarts + 1
            Write-MonitorLog "RESTART #$($state.restarts): $($reason -join ',')"
            Sync-Chi404Code
            Get-LocalDownloadPids | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
            Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -like '*run_local_mbo_keepalive*' } |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
            Start-Sleep -Seconds 2
            Start-LocalKeepalive
            Start-Chi404Keepalive
            $state.last_progress_utc = (Get-Date).ToUniversalTime().ToString("o")
        }

        if ($remaining -le 0 -and $progress) {
            Write-MonitorLog "COMPLETE $billed/$total priority slots billed"
        }

        @{
            supervisor_pid    = $PID
            updated_utc         = (Get-Date).ToUniversalTime().ToString("o")
            billed_in_scope     = $billed
            pct                 = $pct
            remaining           = $remaining
            spent_usd           = $spent
            local_downloaders   = $localPids.Count
            chi404_alive        = $chiAlive
            local_keepalive     = [bool]$keepAlive
            stall_minutes       = [math]::Round($stallMin, 1)
            restarts            = [int]$state.restarts
            last_action         = if ($needsRestart) { "restart: $($reason -join ',')" } else { "ok" }
        } | ConvertTo-Json | Set-Content -Path $StatusPath -Encoding UTF8

        $state | ConvertTo-Json | Set-Content -Path $StatePath -Encoding UTF8
        Start-Sleep -Seconds $PollSeconds
    }
} finally {
    if ($locked) { $mutex.ReleaseMutex() | Out-Null }
    $mutex.Dispose()
}
