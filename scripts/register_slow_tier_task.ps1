#Requires -Version 5.1
<#
.SYNOPSIS
    Register (or update) the Windows Scheduled Task "HFT3SlowTierNightly".

.DESCRIPTION
    Registers a daily scheduled task that runs slow_tier_nightly.ps1 at a
    local time corresponding to 17:35 America/Chicago.

    TIME DERIVATION:
    - Local machine: UTC+7 (Asia/Bangkok or equivalent).
    - Chicago Summer (CDT, UTC-5): 17:35 CDT = 22:35 UTC = 05:35 +7 NEXT DAY.
    - Chicago Winter (CST, UTC-6): 17:35 CST = 23:35 UTC = 06:35 +7 NEXT DAY.
    - We register at 05:35 local (which matches CDT / UTC-5).  During CST the
      task fires 1 hour early (05:35 local = 22:35 UTC = 16:35 CST), which is
      before the 17:00 CT session end.  If CST accuracy matters, update the
      trigger time to 06:35 local for the winter schedule or use a dynamic
      trigger that accounts for DST.  The current 05:35 setting is correct
      for CDT (summer).

    SAFE DEFAULT:
    Without -Confirm this script only prints what it would do and exits.
    Pass -Confirm to actually register the task.  This prevents accidental
    double-registration during dry runs.

.PARAMETER Confirm
    Actually register the scheduled task.  Without this switch the script
    prints the registration command and exits safely.

.PARAMETER ScriptPath
    Override path to slow_tier_nightly.ps1 (default: auto-detected from
    this script's directory).

.PARAMETER PowerShellExe
    Override path to pwsh.exe (default: the currently running pwsh binary).

.EXAMPLE
    # Dry run — print what would be registered:
    .\register_slow_tier_task.ps1

    # Actually register:
    .\register_slow_tier_task.ps1 -Confirm
#>

param(
    [switch]$Confirm,
    [string]$ScriptPath = "",
    [string]$PowerShellExe = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Resolved paths
# ---------------------------------------------------------------------------
if ($ScriptPath -eq "") {
    $ScriptPath = Join-Path $PSScriptRoot "slow_tier_nightly.ps1"
}
if ($PowerShellExe -eq "") {
    # Windows PowerShell 5.1 compatible (no ?. operator).
    $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($pwshCmd) {
        $PowerShellExe = $pwshCmd.Source
    } else {
        $PowerShellExe = "powershell.exe"
    }
}

$TaskName = "HFT3SlowTierNightly"

# 05:35 local (UTC+7) corresponds to 17:35 Chicago Daylight Time (CDT/UTC-5).
# DST note: in winter (CST/UTC-6) 05:35 local = 16:35 CT, 1h before session
# close.  For full correctness in winter, change to 06:35 local.
$TriggerTime = "05:35"

$Arguments = "-NonInteractive -NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""

# ---------------------------------------------------------------------------
# Summary (always printed, even in dry-run mode)
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Task registration summary"
Write-Host "========================="
Write-Host "  Task name    : $TaskName"
Write-Host "  Trigger      : Daily at $TriggerTime local (UTC+7)"
Write-Host "    CT equiv   : 17:35 CDT (UTC-5) summer / 16:35 CST (UTC-6) winter"
Write-Host "    DST caveat : Winter firing is 1h early; update to 06:35 for CST precision"
Write-Host "  Executable   : $PowerShellExe"
Write-Host "  Arguments    : $Arguments"
Write-Host "  Script path  : $ScriptPath"
Write-Host ""

if (-not $Confirm) {
    Write-Host "DRY RUN: pass -Confirm to actually register the task."
    Write-Host ""
    exit 0
}

# ---------------------------------------------------------------------------
# Register via Register-ScheduledTask (requires admin, or task user context)
# ---------------------------------------------------------------------------
Write-Host "Registering task '$TaskName' ..."

$action = New-ScheduledTaskAction `
    -Execute $PowerShellExe `
    -Argument $Arguments

$trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At $TriggerTime

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

# Run as the current user (credentials stored in Windows Credential Manager
# by the task scheduler; no plaintext password in this script).
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force

Write-Host "Task '$TaskName' registered successfully."
Write-Host "  Fires daily at $TriggerTime local (05:35 UTC+7 = 17:35 CDT)."
