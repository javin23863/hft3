#Requires -Version 5.1
# Forward BMC IPMI (623) from Cambodia via CHI404 SSH jump.
param(
    [string]$HostName = "chi404",
    [int]$LocalPort = 1623,
    [string]$BmcHost = "10.10.91.93",
    [int]$BmcPort = 623
)

$ErrorActionPreference = "Stop"

Write-Host @"

CHI404 BMC IPMI tunnel
======================
Keep this window open. From another terminal (with ipmitool installed):

  ipmitool -I lanplus -H 127.0.0.1 -p $LocalPort -U admin -P <HFT3_BMC_PASSWORD> chassis power status
  ipmitool -I lanplus -H 127.0.0.1 -p $LocalPort -U admin -P <HFT3_BMC_PASSWORD> sol activate

Password is in /root/hft3/.env on CHI404 (HFT3_BMC_PASSWORD).

Press Ctrl+C to close the tunnel.

"@

ssh -o ConnectTimeout=15 -N -L "${LocalPort}:${BmcHost}:${BmcPort}" $HostName
