#Requires -Version 5.1
<#
.SYNOPSIS
    Register (or update) the Windows Scheduled Task "HFT3ModelMaintenanceNightly".
.DESCRIPTION
    Daily task running orchestrator_nightly.ps1 at 05:50 local (15 min after the
    slow-tier task at 05:35, so F1 labels exist first). Autonomy is OFF by
    default, so the tick is a safe no-op until you deliberately enable it.

    SAFE DEFAULT: without -Confirm this only prints what it would do.
.PARAMETER Confirm
    Actually register the task.
#>
param(
    [switch]$Confirm,
    [string]$ScriptPath = "",
    [string]$PowerShellExe = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($ScriptPath -eq "") { $ScriptPath = Join-Path $PSScriptRoot "orchestrator_nightly.ps1" }
if ($PowerShellExe -eq "") {
    $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($pwshCmd) { $PowerShellExe = $pwshCmd.Source } else { $PowerShellExe = "powershell.exe" }
}

$TaskName = "HFT3ModelMaintenanceNightly"
$TriggerTime = "05:50"  # 15 min after HFT3SlowTierNightly (05:35)
$Arguments = "-NonInteractive -NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""

Write-Host ""
Write-Host "Task registration summary"
Write-Host "========================="
Write-Host "  Task name   : $TaskName"
Write-Host "  Trigger     : Daily at $TriggerTime local (15 min after slow-tier)"
Write-Host "  Executable  : $PowerShellExe"
Write-Host "  Arguments   : $Arguments"
Write-Host "  Note        : autonomy OFF by default => tick is a safe no-op"
Write-Host ""

if (-not $Confirm) {
    Write-Host "DRY RUN: pass -Confirm to actually register the task."
    exit 0
}

$action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $Arguments
$trigger = New-ScheduledTaskTrigger -Daily -At $TriggerTime
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 4) -StartWhenAvailable -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force

Write-Host "Task '$TaskName' registered (fires daily at $TriggerTime local)."
