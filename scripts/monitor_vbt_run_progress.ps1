# Poll VBT paid-screen progress on Vast (workstation monitor).
param(
    [string]$Repo = "C:\Users\MSI\repos\hft3",
    [string]$SshHost = "",
    [Alias("Port")]
    [int]$SshPort = 0,
    [string]$TmuxSession = "",
    [string]$Pattern = "",
    [string]$HostLabel = "",
    [string]$DeclarationPath = "",
    [int]$IntervalSec = -1,
    [int]$MaxRounds = -1
)

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
        [string[]]$DeclarationNames
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
    $envList = ($EnvNames -join ", ")
    $declList = ($DeclarationNames -join ", ")
    throw "Missing $Label. Pass a parameter, set env ($envList), or add current declaration field ($declList)."
}

function Resolve-RequiredInt {
    param(
        [string]$Label,
        [int]$ParamValue,
        [string[]]$EnvNames,
        [object]$Declaration,
        [string[]]$DeclarationNames
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
    $envList = ($EnvNames -join ", ")
    $declList = ($DeclarationNames -join ", ")
    throw "Missing $Label. Pass a parameter, set env ($envList), or add current declaration field ($declList)."
}

if ($DeclarationPath -eq "") {
    if ($env:VBT_FULL_RUN_DECLARATION) {
        $DeclarationPath = $env:VBT_FULL_RUN_DECLARATION
    } else {
        $DeclarationPath = Join-Path $Repo "runtime\reports\vbt_full_run_declaration.json"
    }
}
$Declaration = Read-JsonFile -PathValue $DeclarationPath

$Port = Resolve-RequiredInt -Label "Vast SSH port" -ParamValue $SshPort -EnvNames @("VAST_SSH_PORT") -Declaration $Declaration -DeclarationNames @("ssh_port", "vast_ssh_port")
$SshHost = Resolve-RequiredString -Label "Vast SSH host" -ParamValue $SshHost -EnvNames @("VAST_SSH_HOST", "VAST_SSH_TARGET") -Declaration $Declaration -DeclarationNames @("ssh_host", "vast_ssh_host", "ssh_target", "vast_ssh_target")
$TmuxSession = Resolve-RequiredString -Label "VBT tmux session" -ParamValue $TmuxSession -EnvNames @("VBT_TMUX_SESSION") -Declaration $Declaration -DeclarationNames @("tmux_session", "vbt_tmux_session")
$Pattern = Resolve-RequiredString -Label "VBT run pattern/run id" -ParamValue $Pattern -EnvNames @("VBT_RUN_PATTERN", "VBT_FULL_RUN_ID", "VBT_RUN_ID") -Declaration $Declaration -DeclarationNames @("run_pattern", "vbt_run_pattern", "run_id", "vbt_full_run_id")
$IntervalSec = if ($IntervalSec -ge 0) { $IntervalSec } elseif ($env:VBT_MONITOR_INTERVAL_SEC) { [int]$env:VBT_MONITOR_INTERVAL_SEC } else { 120 }
$MaxRounds = if ($MaxRounds -ge 0) { $MaxRounds } elseif ($env:VBT_MONITOR_MAX_ROUNDS) { [int]$env:VBT_MONITOR_MAX_ROUNDS } else { 9999 }
$AuditModeArg = if ($env:VBT_MONITOR_FULL_AUDIT -eq "1") { "" } else { "--fast-status" }
if ($HostLabel -eq "") {
    if ($env:VBT_HOST_LABEL) {
        $HostLabel = $env:VBT_HOST_LABEL
    } else {
        $hostVcpu = Get-JsonValue -ObjectValue $Declaration -Names @("host_vcpu")
        $HostLabel = if ($hostVcpu) { "Vast $hostVcpu CPU" } else { "Vast host" }
    }
}

Set-Location $Repo
if ($MaxRounds -le 0) {
    Write-Host "VBT monitor MaxRounds=$MaxRounds; metadata resolved; no SSH/SCP attempted."
    exit 0
}

scp -o ConnectTimeout=15 -P $Port scripts/audit_vbt_run_progress.py "${SshHost}:/root/hft3/repo/scripts/" | Out-Null

for ($round = 1; $round -le $MaxRounds; $round++) {
    $ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss UTC")
    Write-Host "=== monitor round $round/$MaxRounds $ts ==="
    $escHostLabel = "'" + ($HostLabel -replace "'", "'\''") + "'"
    $escSshHost = "'" + ($SshHost -replace "'", "'\''") + "'"
    $escTmux = "'" + ($TmuxSession -replace "'", "'\''") + "'"
    $escPattern = "'" + ($Pattern -replace "'", "'\''") + "'"
    ssh -o ConnectTimeout=15 -p $Port $SshHost "cd /root/hft3/repo && export PYTHONPATH=/root/hft3/repo:/root/hft3/repo/packages VBT_HOST_LABEL=$escHostLabel VAST_SSH_HOST=$escSshHost VBT_TMUX_SESSION=$escTmux && python3 scripts/audit_vbt_run_progress.py --pattern $escPattern $AuditModeArg"
    scp -o ConnectTimeout=15 -P $Port "${SshHost}:/root/hft3/repo/runtime/reports/vbt_run_progress_audit.json" "$Repo\runtime\reports\vbt_run_progress_audit.json" 2>$null | Out-Null
    scp -o ConnectTimeout=15 -P $Port "${SshHost}:/root/hft3/repo/runtime/reports/vbt_full_status.json" "$Repo\runtime\reports\vbt_full_status.json" 2>$null | Out-Null
    if ($round -lt $MaxRounds) { Start-Sleep -Seconds $IntervalSec }
}
