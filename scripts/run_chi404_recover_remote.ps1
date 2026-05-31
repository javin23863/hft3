#Requires -Version 5.1
# Poll CHI404 SSH; on reconnect sync and run 24_recover_boot_to_disk.sh.
param(
    [string]$HostName = "chi404",
    [int]$MaxAttempts = 120,
    [int]$IntervalSec = 15,
    [switch]$RunExpoAfterRecovery
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$RepoRemote = "/root/hft3/repo"
$Log = Join-Path $Repo "runtime\chi404\recovery_poll.log"
$Lock = Join-Path $Repo "runtime\chi404\recovery_poll.lock"
$SshOpts = @("-o", "ConnectTimeout=15")

if (Test-Path $Lock) {
    $lockPid = Get-Content $Lock -ErrorAction SilentlyContinue
    if ($lockPid -and (Get-Process -Id $lockPid -ErrorAction SilentlyContinue)) {
        Write-Host "Recovery poll already running (PID $lockPid). Log: $Log"
        exit 0
    }
}
$PID | Out-File $Lock -Force

try {
"=== recover poll $(Get-Date -Format o) pid=$PID ===" | Out-File $Log

for ($i = 1; $i -le $MaxAttempts; $i++) {
    $ts = Get-Date -Format "HH:mm:ss"
    $tcp = New-Object System.Net.Sockets.TcpClient
    $up = $false
    try {
        $iar = $tcp.BeginConnect("64.44.98.219", 22, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(5000, $false)) {
            $tcp.EndConnect($iar)
            $up = $true
        }
    } catch { } finally { $tcp.Close() }

    if (-not $up) {
        "$ts attempt $i down" | Out-File $Log -Append
        Start-Sleep -Seconds $IntervalSec
        continue
    }

    "$ts attempt $i UP - running recovery" | Tee-Object -FilePath $Log -Append
    ssh @SshOpts $HostName "mkdir -p $RepoRemote/infrastructure/chi404" 2>&1 | Out-File $Log -Append
    scp @SshOpts "$Repo\infrastructure\chi404\24_recover_boot_to_disk.sh" `
        "$Repo\infrastructure\chi404\17a_oob_preflight.sh" `
        "$Repo\infrastructure\chi404\25_expo_sol_preflight.sh" `
        "${HostName}:$RepoRemote/infrastructure/chi404/" 2>&1 | Out-File $Log -Append

    $expoBlock = ""
    if ($RunExpoAfterRecovery) {
        $expoBlock = @"

echo '=== OOB preflight before EXPO ==='
bash $RepoRemote/infrastructure/chi404/17a_oob_preflight.sh
export HFT3_OOB_CONFIRMED=1
bash $RepoRemote/infrastructure/chi404/25_expo_sol_preflight.sh
"@
    }

    $remote = @"
sed -i 's/\r$//' $RepoRemote/infrastructure/chi404/*.sh
bash $RepoRemote/infrastructure/chi404/24_recover_boot_to_disk.sh
sleep 90
uptime
dmidecode -t memory 2>/dev/null | grep 'Configured Memory Speed' | head -1
$expoBlock
"@
    ssh @SshOpts $HostName $remote 2>&1 | Tee-Object -FilePath $Log -Append
    Write-Host "Recovery applied. See $Log"
    exit 0
}

Write-Error "CHI404 did not return after $MaxAttempts attempts. Run: powershell -File scripts/run_chi404_oob_recovery.ps1"
exit 1
} finally {
    Remove-Item $Lock -Force -ErrorAction SilentlyContinue
}
