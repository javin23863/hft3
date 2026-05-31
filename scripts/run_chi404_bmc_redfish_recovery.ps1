#Requires -Version 5.1
# Boot-to-disk via BMC Redfish — no SSH required if BMC IP is routable from this PC.
param(
    [string]$BmcHost = $env:HFT3_BMC_PUBLIC_IP,
    [string]$BmcUser = "admin",
    [string]$BmcPassword = $env:HFT3_BMC_PASSWORD,
    [switch]$NoReboot
)

$ErrorActionPreference = "Stop"
if (-not $BmcHost) { $BmcHost = $env:HFT3_BMC_IP }
if (-not $BmcHost) { $BmcHost = "10.10.91.93" }
if (-not $BmcPassword) {
    Write-Error "Set HFT3_BMC_PASSWORD in .env (same as /root/hft3/.env on CHI404)"
}

$Base = "https://${BmcHost}/redfish/v1"
$Auth = "${BmcUser}:${BmcPassword}"

Write-Host "Redfish recover via BMC $BmcHost ..."

$probe = curl.exe -sk -m 10 -u $Auth -o NUL -w "%{http_code}" "${Base}/Managers/Self" 2>&1
if ($probe -ne "200") {
    Write-Warning "BMC not reachable at $BmcHost (HTTP $probe). Use QuantVPS portal IPMI or set HFT3_BMC_PUBLIC_IP."
    exit 1
}

curl.exe -sk -m 15 -u $Auth -X PATCH `
    -H "Content-Type: application/json" `
    -d '{"Boot":{"BootSourceOverrideEnabled":"Disabled","BootSourceOverrideTarget":"None"}}' `
    "${Base}/Systems/Self" 2>&1 | Write-Host

if (-not $NoReboot) {
    Write-Host "ForceRestart..."
    curl.exe -sk -m 15 -u $Auth -X POST `
        -H "Content-Type: application/json" `
        -d '{"ResetType":"ForceRestart"}' `
        "${Base}/Systems/Self/Actions/ComputerSystem.Reset" 2>&1 | Write-Host
}

Write-Host "REDFISH_RECOVER=SENT via $BmcHost"
