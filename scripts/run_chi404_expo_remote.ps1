#Requires -Version 5.1
# Sync and run EXPO SOL automation on CHI404 (host must be up; run OOB preflight first).
param(
    [string]$HostName = "chi404"
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$RepoRemote = "/root/hft3/repo"
$SshOpts = @("-o", "ConnectTimeout=15")

ssh @SshOpts $HostName "mkdir -p $RepoRemote/infrastructure/chi404"
scp @SshOpts "$Repo\infrastructure\chi404\17a_oob_preflight.sh" `
    "$Repo\infrastructure\chi404\25_expo_sol_preflight.sh" `
    "${HostName}:$RepoRemote/infrastructure/chi404/"

$remote = @"
sed -i 's/\r$//' $RepoRemote/infrastructure/chi404/*.sh
bash $RepoRemote/infrastructure/chi404/17a_oob_preflight.sh
export HFT3_OOB_CONFIRMED=1
bash $RepoRemote/infrastructure/chi404/25_expo_sol_preflight.sh
"@
$output = ssh @SshOpts $HostName $remote 2>&1
$output | ForEach-Object { Write-Host $_ }
if (($output -join "`n") -notmatch 'OC_VERIFY=PASS') {
    Write-Error "EXPO verify did not PASS"
}
