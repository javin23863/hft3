#Requires -Version 5.1
<#
.SYNOPSIS
    Register (or update) the Windows Scheduled Task "HFT3Cockpit" — run the
    cockpit dashboard backend as a long-running, restart-on-failure service.

.DESCRIPTION
    Registers a task that launches scripts/run_cockpit.ps1 at user logon and
    keeps it running: no execution time limit, auto-restart on failure. The
    backend binds 127.0.0.1:8080 (single-origin SPA); put Caddy in front for TLS
    / remote access (see configs/caddy/Caddyfile.example). The control plane
    stays local-origin only regardless.

    SAFE DEFAULT: without -Confirm this only prints what it would do and exits.

.PARAMETER Confirm
    Actually register the scheduled task.

.PARAMETER ScriptPath
    Override path to run_cockpit.ps1 (default: auto-detected next to this script).

.PARAMETER PowerShellExe
    Override path to the PowerShell binary (default: the running pwsh, else powershell.exe).

.EXAMPLE
    .\register_cockpit_task.ps1            # dry run
    .\register_cockpit_task.ps1 -Confirm   # register
#>

param(
    [switch]$Confirm,
    [string]$ScriptPath = "",
    [string]$PowerShellExe = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($ScriptPath -eq "") {
    $ScriptPath = Join-Path $PSScriptRoot "run_cockpit.ps1"
}
if ($PowerShellExe -eq "") {
    $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($pwshCmd) { $PowerShellExe = $pwshCmd.Source } else { $PowerShellExe = "powershell.exe" }
}

$TaskName  = "HFT3Cockpit"
$Arguments = "-NonInteractive -NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""

Write-Host ""
Write-Host "Task registration summary"
Write-Host "========================="
Write-Host "  Task name    : $TaskName"
Write-Host "  Trigger      : At user logon (long-running service)"
Write-Host "  Restart      : on failure, every 1 min, up to 999 times"
Write-Host "  Time limit   : none (runs until stopped)"
Write-Host "  Executable   : $PowerShellExe"
Write-Host "  Arguments    : $Arguments"
Write-Host "  Binds        : 127.0.0.1:8080 (front with Caddy for TLS/remote)"
Write-Host ""

if (-not $Confirm) {
    Write-Host "DRY RUN: pass -Confirm to actually register the task."
    Write-Host ""
    exit 0
}

Write-Host "Registering task '$TaskName' ..."

$action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $Arguments

$trigger = New-ScheduledTaskTrigger -AtLogOn

# ExecutionTimeLimit 0 = no limit (long-running); restart on failure.
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force

Write-Host "Task '$TaskName' registered. Starting it now ..."
Start-ScheduledTask -TaskName $TaskName
Write-Host "Cockpit service launched (http://127.0.0.1:8080). Stop with: Stop-ScheduledTask -TaskName $TaskName"
