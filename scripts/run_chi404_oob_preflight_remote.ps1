#Requires -Version 5.1
# Run OOB preflight on CHI404; optionally probe local iKVM tunnel.
param(
    [string]$HostName = "chi404",
    [switch]$ProbeLocalTunnel
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$RepoRemote = "/root/hft3/repo"
$SshOpts = @("-o", "ConnectTimeout=15")

Write-Host "Syncing OOB scripts to $HostName ..."
ssh @SshOpts $HostName "mkdir -p $RepoRemote/infrastructure/chi404"
scp @SshOpts "$Repo\infrastructure\chi404\17a_oob_preflight.sh" `
    "${HostName}:$RepoRemote/infrastructure/chi404/"

$output = ssh @SshOpts $HostName "sed -i 's/\r$//' $RepoRemote/infrastructure/chi404/17a_oob_preflight.sh; bash $RepoRemote/infrastructure/chi404/17a_oob_preflight.sh" 2>&1
$output | ForEach-Object { Write-Host $_ }

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (($output -join "`n") -notmatch 'OOB_PREFLIGHT=PASS') {
    Write-Error "OOB preflight did not PASS on host"
}

if ($ProbeLocalTunnel) {
    $code = curl.exe -sk -o NUL -w "%{http_code}" --connect-timeout 3 "https://localhost:8443/" 2>&1
    Write-Host "Local iKVM tunnel (8443): HTTP $code"
    if ($code -ne "200") {
        Write-Warning "Start: powershell -File scripts/run_chi404_bmc_ikvm_tunnel.ps1"
    }
}

Write-Host "Workstation OOB checklist:"
Write-Host "  1. powershell -File scripts/run_chi404_bmc_ikvm_tunnel.ps1   (8443)"
Write-Host "  2. powershell -File scripts/run_chi404_bmc_ipmi_tunnel.ps1   (1623)"
Write-Host "  3. export HFT3_OOB_CONFIRMED=1 on CHI404 before BIOS reboot"
