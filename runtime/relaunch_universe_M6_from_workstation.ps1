# Start Vast instance (if needed), push fixes, relaunch universe_M6_full at max stable workers.
$ErrorActionPreference = "Stop"
$InstanceId = if ($env:HFT3_VAST_INSTANCE_ID) { $env:HFT3_VAST_INSTANCE_ID } else { "41383988" }
$Repo = "C:\Users\MSI\repos\hft3"
$MaxStartWaitMin = 15
$MonitorSec = 300

function Get-VastSshTarget {
    param([string]$Id)
    $raw = (& vastai show instance $Id --raw 2>$null | Out-String).Trim()
    if (-not $raw) { throw "vastai show instance $Id failed" }
    $obj = $raw | ConvertFrom-Json
    $status = $obj.actual_status
    if ($obj.ssh_host -and $obj.ssh_port) {
        return @{
            Status = $status
            Host = "root@$($obj.ssh_host)"
            Port = [int]$obj.ssh_port
            CpuCores = [int]$obj.cpu_cores
        }
    }
    $url = (& vastai ssh-url $Id 2>$null | Out-String).Trim()
    if ($url -match 'ssh://([^@]+)@([^:]+):(\d+)') {
        return @{
            Status = $status
            Host = "$($Matches[1])@$($Matches[2])"
            Port = [int]$Matches[3]
            CpuCores = 256
        }
    }
    throw "Could not resolve SSH target for instance $Id"
}

function Wait-VastRunning {
    param([string]$Id, [int]$MaxMin)
    $deadline = (Get-Date).AddMinutes($MaxMin)
    $attempt = 0
    while ((Get-Date) -lt $deadline) {
        $attempt++
        $t = Get-VastSshTarget -Id $Id
        Write-Host "poll $attempt status=$($t.Status) ssh=$($t.Host):$($t.Port)"
        if ($t.Status -eq "running") { return $t }
        if ($t.Status -in @("exited", "stopped")) {
            Write-Host "starting instance $Id ..."
            & vastai start instance $Id 2>&1 | Out-Host
        }
        Start-Sleep -Seconds 30
    }
    throw "Instance $Id not running after ${MaxMin}m (last status=$($t.Status))"
}

Write-Host "=== Vast M6 relaunch instance=$InstanceId ==="
$tgt = Wait-VastRunning -Id $InstanceId -MaxMin $MaxStartWaitMin
$HostName = $tgt.Host
$Port = $tgt.Port
$ssh = "ssh -o ConnectTimeout=20 -o StrictHostKeyChecking=no -p $Port $HostName"
$scp = "scp -o ConnectTimeout=20 -o StrictHostKeyChecking=no -P $Port"

& cmd /c "$ssh `"echo OK; nproc; ulimit -u; test -d /data/npz && find /data/npz -maxdepth 1 -type f | wc -l`""

$files = @(
    "$Repo\runtime\launch_universe_M6_full.sh",
    "$Repo\runtime\relaunch_universe_M6_vast.sh",
    "$Repo\runtime\monitor\watch_universe_M6_full.sh",
    "$Repo\scripts\run_event_universe.py"
)
foreach ($f in $files) {
    $dest = $f.Replace("$Repo\", "/root/hft3/repo/").Replace("\", "/")
    & cmd /c "$scp `"$f`" ${HostName}:$dest"
}

& cmd /c "$ssh `"chmod +x /root/hft3/repo/runtime/launch_universe_M6_full.sh /root/hft3/repo/runtime/relaunch_universe_M6_vast.sh /root/hft3/repo/runtime/monitor/watch_universe_M6_full.sh`""
& cmd /c "$ssh `"bash /root/hft3/repo/runtime/relaunch_universe_M6_vast.sh`""

Write-Host "Monitoring ${MonitorSec}s ..."
Start-Sleep -Seconds $MonitorSec

& cmd /c "$ssh `"LOG=\$(cat /root/hft3/repo/runtime/.universe_M6_full_latest_log) bash /root/hft3/repo/runtime/monitor/watch_universe_M6_full.sh; pgrep -c -f run_event_universe || true; tail -n 25 \$(cat /root/hft3/repo/runtime/.universe_M6_full_latest_log)`""

$latest = (& cmd /c "$ssh `"cat /root/hft3/repo/runtime/.universe_M6_full_latest_log 2>/dev/null || echo`"").Trim()
if ($latest) {
    & cmd /c "$scp ${HostName}:$latest `"$Repo\runtime\universe_M6_vast.log`""
    Write-Host "Copied log -> $Repo\runtime\universe_M6_vast.log"
}

Write-Host "=== done ssh=$HostName`:$Port ==="
