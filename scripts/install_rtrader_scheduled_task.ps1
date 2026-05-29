# Install Windows Scheduled Task to run Rithmic trial capture unattended at logon.
param(
    [string]$TaskName = 'HFT3-RithmicTrialCapture'
)
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $RepoRoot 'scripts\rtrader_run_unattended.ps1'

if (-not (Test-Path $Runner)) {
    throw "Missing runner: $Runner"
}

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force
Write-Host "Registered scheduled task: $TaskName (runs at logon)"
Write-Host "One-time GUI login to R|Trader may still be required before fully unattended operation."
