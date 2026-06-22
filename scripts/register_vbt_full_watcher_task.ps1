#Requires -Version 5.1
<#
.SYNOPSIS
    Register the Windows Scheduled Task "HFT3VastVbtFullWatcher".

.DESCRIPTION
    Registers a local observation-only watcher for the active Vast VectorBT
    paid-screen run. Safe default: without -Confirm this prints the task that
    would be registered and exits without changing Task Scheduler.
#>

param(
    [switch]$Confirm,
    [string]$TaskName = "HFT3VastVbtFullWatcher",
    [string]$ScriptPath = "",
    [string]$PowerShellExe = "",
    [string]$DeclarationPath = "",
    [string]$StatusPath = "",
    [string]$SshHost = "",
    [int]$SshPort = 0,
    [string]$TmuxSession = "",
    [string]$RunId = "",
    [int]$ExpectedWorkUnits = 0,
    [int]$IntervalMinutes = 10
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Read-JsonFile {
    param([string]$PathValue)
    if (-not (Test-Path $PathValue)) {
        return $null
    }
    return Get-Content -Raw -Path $PathValue | ConvertFrom-Json
}

function Get-JsonValue {
    param(
        [object]$ObjectValue,
        [string[]]$Names
    )
    if ($null -eq $ObjectValue) {
        return $null
    }
    foreach ($name in $Names) {
        $prop = $ObjectValue.PSObject.Properties[$name]
        if ($null -ne $prop -and $null -ne $prop.Value -and -not ($prop.Value -is [string] -and $prop.Value -eq "")) {
            return $prop.Value
        }
    }
    return $null
}

function Resolve-RequiredString {
    param(
        [string]$Label,
        [string]$ParamValue,
        [string[]]$EnvNames,
        [object]$Declaration,
        [string[]]$DeclarationNames,
        [object]$Status,
        [string[]]$StatusNames
    )
    if ($ParamValue -ne "") {
        return $ParamValue
    }
    foreach ($envName in $EnvNames) {
        $envValue = [Environment]::GetEnvironmentVariable($envName)
        if ($envValue) {
            return $envValue
        }
    }
    $declValue = Get-JsonValue -ObjectValue $Declaration -Names $DeclarationNames
    if ($declValue) {
        return [string]$declValue
    }
    $statusValue = Get-JsonValue -ObjectValue $Status -Names $StatusNames
    if ($statusValue) {
        return [string]$statusValue
    }
    $envList = ($EnvNames -join ", ")
    $declList = ($DeclarationNames -join ", ")
    $statusList = ($StatusNames -join ", ")
    throw "Missing $Label. Pass a parameter, set env ($envList), add current declaration field ($declList), or add current status field ($statusList)."
}

function Resolve-RequiredInt {
    param(
        [string]$Label,
        [int]$ParamValue,
        [string[]]$EnvNames,
        [object]$Declaration,
        [string[]]$DeclarationNames,
        [object]$Status,
        [string[]]$StatusNames
    )
    if ($ParamValue -gt 0) {
        return $ParamValue
    }
    foreach ($envName in $EnvNames) {
        $envValue = [Environment]::GetEnvironmentVariable($envName)
        if ($envValue) {
            return [int]$envValue
        }
    }
    $declValue = Get-JsonValue -ObjectValue $Declaration -Names $DeclarationNames
    if ($declValue) {
        return [int]$declValue
    }
    $statusValue = Get-JsonValue -ObjectValue $Status -Names $StatusNames
    if ($statusValue) {
        return [int]$statusValue
    }
    $envList = ($EnvNames -join ", ")
    $declList = ($DeclarationNames -join ", ")
    $statusList = ($StatusNames -join ", ")
    throw "Missing $Label. Pass a parameter, set env ($envList), add current declaration field ($declList), or add current status field ($statusList)."
}

if ($ScriptPath -eq "") {
    $ScriptPath = Join-Path $PSScriptRoot "watch_vbt_full_run.ps1"
}
if ($PowerShellExe -eq "") {
    $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($pwshCmd) {
        $PowerShellExe = $pwshCmd.Source
    } else {
        $PowerShellExe = "powershell.exe"
    }
}

if (-not (Test-Path $ScriptPath)) {
    throw "watcher script not found: $ScriptPath"
}
if ($IntervalMinutes -lt 1) {
    throw "IntervalMinutes must be >= 1"
}

if ($DeclarationPath -eq "") {
    if ($env:VBT_FULL_RUN_DECLARATION) {
        $DeclarationPath = $env:VBT_FULL_RUN_DECLARATION
    } else {
        $DeclarationPath = Join-Path (Split-Path -Parent $PSScriptRoot) "runtime\reports\vbt_full_run_declaration.json"
    }
}
$Declaration = Read-JsonFile -PathValue $DeclarationPath
if ($StatusPath -eq "") {
    if ($env:VBT_FULL_STATUS_PATH) {
        $StatusPath = $env:VBT_FULL_STATUS_PATH
    } elseif ($env:VBT_STATUS_FILE) {
        $StatusPath = $env:VBT_STATUS_FILE
    } else {
        $StatusPath = Join-Path (Split-Path -Parent $PSScriptRoot) "runtime\reports\vbt_full_status.json"
    }
}
$Status = Read-JsonFile -PathValue $StatusPath

$SshHost = Resolve-RequiredString -Label "Vast SSH host" -ParamValue $SshHost -EnvNames @("VAST_SSH_HOST", "VAST_SSH_TARGET") -Declaration $Declaration -DeclarationNames @("ssh_host", "vast_ssh_host", "ssh_target", "vast_ssh_target") -Status $Status -StatusNames @("ssh_host", "vast_ssh_host", "ssh_target", "vast_ssh_target", "host")
$SshPort = Resolve-RequiredInt -Label "Vast SSH port" -ParamValue $SshPort -EnvNames @("VAST_SSH_PORT") -Declaration $Declaration -DeclarationNames @("ssh_port", "vast_ssh_port") -Status $Status -StatusNames @("ssh_port", "vast_ssh_port", "port")
$TmuxSession = Resolve-RequiredString -Label "VBT tmux session" -ParamValue $TmuxSession -EnvNames @("VBT_TMUX_SESSION") -Declaration $Declaration -DeclarationNames @("tmux_session", "vbt_tmux_session") -Status $Status -StatusNames @("tmux_session", "vbt_tmux_session", "session", "session_name")
$RunId = Resolve-RequiredString -Label "VBT run id" -ParamValue $RunId -EnvNames @("VBT_FULL_RUN_ID", "VBT_RUN_ID") -Declaration $Declaration -DeclarationNames @("run_id", "vbt_full_run_id") -Status $Status -StatusNames @("run_id", "vbt_full_run_id", "vbt_run_id")
$ExpectedWorkUnits = Resolve-RequiredInt -Label "expected work units" -ParamValue $ExpectedWorkUnits -EnvNames @("VBT_EXPECTED_WORK_UNITS", "VBT_FULL_EXPECTED_WORK_UNITS") -Declaration $Declaration -DeclarationNames @("expected_work_units") -Status $Status -StatusNames @("expected_work_units", "expected", "expected_units", "total_work_units", "work_units_expected")

$arguments = @(
    "-NonInteractive",
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$ScriptPath`"",
    "-SshHost", "`"$SshHost`"",
    "-SshPort", $SshPort,
    "-TmuxSession", "`"$TmuxSession`"",
    "-RunId", "`"$RunId`"",
    "-ExpectedWorkUnits", $ExpectedWorkUnits
) -join " "

$declarationState = if ($null -ne $Declaration) { "found" } else { "missing" }
$statusState = if ($null -ne $Status) { "found" } else { "missing" }

Write-Host ""
Write-Host "Task registration summary"
Write-Host "========================="
Write-Host "  Task name   : $TaskName"
Write-Host "  Trigger     : every $IntervalMinutes minutes, starting about 1 minute after registration"
Write-Host "  Executable  : $PowerShellExe"
Write-Host "  Arguments   : $arguments"
Write-Host "  Run id      : $RunId"
Write-Host "  Vast host   : ${SshHost}:$SshPort"
Write-Host "  Tmux        : $TmuxSession"
Write-Host "  Metadata    : declaration=$declarationState ($DeclarationPath); status=$statusState ($StatusPath)"
Write-Host "  Output      : runtime/reports/vbt_watcher_status.json"
Write-Host "  Safety      : observation-only; no restart/kill/relaunch"
Write-Host ""

if (-not $Confirm) {
    Write-Host "DRY RUN: pass -Confirm to actually register the task."
    exit 0
}

$action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $arguments
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 8) `
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

Write-Host "Task '$TaskName' registered. First scheduled check starts shortly."
Write-Host "Manual one-shot check: powershell -ExecutionPolicy Bypass -File `"$ScriptPath`""
