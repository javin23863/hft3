#Requires -Version 5.1
# Sync .env + IRQ/net + idle on CHI404; capture baseline JSON for diff.
param(
    [string]$HostName = "chi404"
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$RepoRemote = "/root/hft3/repo"
$Script = "$RepoRemote/infrastructure/chi404/01_fix_baseline_gaps.sh"
$SshOpts = @("-o", "ConnectTimeout=15")

Write-Host "Syncing chi404 scripts to $HostName ..."
ssh @SshOpts $HostName "mkdir -p $RepoRemote/infrastructure/chi404 /root/hft3/logs/baseline_fix"
scp @SshOpts -r "$Repo\infrastructure\chi404" "${HostName}:$RepoRemote/infrastructure/"

Write-Host "Running baseline gap fix on $HostName ..."
$output = ssh @SshOpts $HostName "sed -i 's/\r$//' $RepoRemote/infrastructure/chi404/*.sh 2>/dev/null; bash $Script" 2>&1
$output | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) {
    Write-Error "Baseline fix failed (exit $LASTEXITCODE). Check remote logs under /root/hft3/logs/baseline_fix/"
}

$runMatch = [regex]::Match(($output -join "`n"), 'RUN_ID=(\S+)')
if (-not $runMatch.Success) {
    Write-Error "Could not parse RUN_ID from remote output"
}
$RunId = $runMatch.Groups[1].Value
Write-Host "RUN_ID=$RunId"

$result = ssh @SshOpts $HostName "cat /root/hft3/logs/baseline_fix/${RunId}/result.txt"
$result = $result.Trim()
Write-Host $result
if ($result -ne "BASELINE_FIX=PASS") {
    Write-Error "Remote baseline fix did not PASS: $result"
}

$LocalDir = Join-Path (Join-Path (Join-Path $PSScriptRoot "..") "runtime") "chi404\baseline"
New-Item -ItemType Directory -Force -Path $LocalDir | Out-Null
scp @SshOpts "${HostName}:/root/hft3/logs/baseline_fix/${RunId}/hardware_baseline/baseline.json" `
    (Join-Path $LocalDir "latest_capture.json")

Write-Host "Wrote runtime/chi404/baseline/latest_capture.json (diff vs 2026-05-31T030000Z_baseline.json)"
