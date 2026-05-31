#Requires -Version 5.1
# Post-BIOS OC verify + stability on CHI404 (sync chi404 scripts first).
param(
    [string]$HostName = "chi404",
    [ValidateSet("readiness", "verify", "stability")]
    [string]$Phase = "verify"
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$RepoRemote = "/root/hft3/repo"
$SshOpts = @("-o", "ConnectTimeout=15")

if ($env:HFT3_OC_MARKET_LOAD -eq "1" -and $Phase -eq "verify") {
    $Phase = "stability"
}

$ScriptMap = @{
    readiness = "$RepoRemote/infrastructure/chi404/14_bios_oc_readiness.sh"
    verify    = "$RepoRemote/infrastructure/chi404/15_post_bios_oc_verify.sh"
    stability = "$RepoRemote/infrastructure/chi404/16_oc_stability_under_load.sh"
}

$OcEnv = @(
    "HFT3_OC_MIN_MHZ", "HFT3_OC_TARGET_MHZ", "HFT3_OC_MIN_MEM_MTS",
    "HFT3_OC_STRESS_SEC", "HFT3_OC_MARKET_LOAD", "HFT3_OC_RUN_PAPER_SWEEP",
    "HFT3_OC_REQUIRE_MARKET_LOAD", "RUN_ID"
) | ForEach-Object {
    $val = (Get-Item -Path "env:$_" -ErrorAction SilentlyContinue).Value
    if ($val) { "export $_=$val" }
}

$ExportBlock = ($OcEnv -join "`n")

Write-Host "Syncing chi404 scripts to $HostName ..."
ssh @SshOpts $HostName "mkdir -p $RepoRemote/infrastructure/chi404 /root/hft3/logs/oc"
scp @SshOpts -r "$Repo\infrastructure\chi404" "${HostName}:$RepoRemote/infrastructure/"

$RemoteScript = $ScriptMap[$Phase]
Write-Host "Running phase=$Phase on $HostName ..."
$remote = @"
$ExportBlock
sed -i 's/\r$//' $RepoRemote/infrastructure/chi404/*.sh
bash $RemoteScript
"@
$output = ssh @SshOpts $HostName $remote 2>&1
$output | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$runMatch = [regex]::Match(($output -join "`n"), 'RUN_ID=(\S+)')
if ($runMatch.Success) {
    Write-Host "RUN_ID=$($runMatch.Groups[1].Value)"
}

$joined = $output -join "`n"
if ($Phase -eq "verify" -and $joined -notmatch 'OC_VERIFY=PASS') {
    Write-Error "OC verify did not PASS"
}
if ($Phase -eq "stability" -and $joined -notmatch 'OC_STABILITY=(PASS|JITTER_PASS)') {
    Write-Error "OC stability did not PASS"
}

Write-Host "Done. See /root/hft3/logs/oc/ on $HostName"
