#Requires -Version 5.1
# Try every CHI404 recovery path: SSH, direct Redfish, QuantVPS portal.
param(
    [string]$HostName = "chi404",
    [string]$QuantVpsPortal = $env:HFT3_QUANTVPS_PORTAL
)

$ErrorActionPreference = "Continue"
$Repo = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $Repo ".env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            Set-Item -Path "env:$($Matches[1])" -Value $Matches[2].Trim()
        }
    }
}
if (-not $QuantVpsPortal) { $QuantVpsPortal = "https://www.quantvps.com/login" }

Write-Host "=== CHI404 OOB recovery (multi-path) ==="

# 1) SSH path
Write-Host "[1/3] SSH to $HostName ..."
$sshOk = $false
try {
    $out = ssh -o ConnectTimeout=10 -o BatchMode=yes $HostName "bash /root/hft3/repo/infrastructure/chi404/24_recover_boot_to_disk.sh; uptime" 2>&1
    if ($LASTEXITCODE -eq 0) {
        $sshOk = $true
        $out | ForEach-Object { Write-Host $_ }
        Write-Host "RECOVERY=SSH_OK"
        exit 0
    }
} catch { }
Write-Host "SSH unavailable."

# 2) Direct Redfish to BMC (public or in-band IP if routable)
Write-Host "[2/3] Direct BMC Redfish ..."
try {
    & "$Repo\scripts\run_chi404_bmc_redfish_recovery.ps1" 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -eq 0) {
        Write-Host "RECOVERY=REDFISH_OK"
        exit 0
    }
} catch {
    Write-Host $_.Exception.Message
}
Write-Host "Direct BMC not reachable from this PC."

# 3) QuantVPS portal
Write-Host "[3/3] Opening QuantVPS login: $QuantVpsPortal"
Write-Host "Support docs: https://intercom.help/quantvps"
$ticket = Join-Path $Repo "runtime\chi404\quantvps_remote_hands_ticket.txt"
if (Test-Path $ticket) {
    Set-Clipboard -Value (Get-Content $ticket -Raw)
    Write-Host "Copied remote-hands ticket to clipboard."
}
Start-Process $QuantVpsPortal
Write-Host @"

QuantVPS login opened. For CHI404 bare metal (BIOS stuck / SSH down):
  - Dashboard: https://www.quantvps.com/login
  - Open a support ticket (Help Center) requesting KVM/IPMI or boot-to-disk
  - Server IP: 64.44.98.219 hostname CHI404

After console action, run:
  powershell -File scripts/run_chi404_recover_remote.ps1

"@
exit 1
