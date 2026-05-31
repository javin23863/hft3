#Requires -Version 5.1
# Open BMC iKVM (HTTPS) from Cambodia via CHI404 SSH jump — no colo visit.
param(
    [string]$HostName = "chi404",
    [int]$LocalPort = 8443,
    [string]$BmcHost = "10.10.91.93",
    [int]$BmcPort = 443
)

$ErrorActionPreference = "Stop"

Write-Host @"

CHI404 BMC tunnel (ASRockRack AST2600 iKVM)
============================================
From this PC (Cambodia), keep this window open, then open in your browser:

  https://localhost:$LocalPort

Default BMC login (change after first use): admin / admin
If that fails, ask for HFT3_BMC_PASSWORD in /root/hft3/.env on CHI404.

In iKVM: reboot to BIOS or use Remote Control -> Power -> Reset, then:
  Advanced -> DRAM / EXPO -> enable 4800 profile -> F10 save

After reboot, verify from SSH:
  bash infrastructure/chi404/15_post_bios_oc_verify.sh

Press Ctrl+C to close the tunnel.

"@

ssh -o ConnectTimeout=15 -N -L "${LocalPort}:${BmcHost}:${BmcPort}" $HostName
