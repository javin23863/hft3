#Requires -Version 5.1
<#
.SYNOPSIS
    Observation-only watcher for the active Vast VectorBT paid-screen run.

.DESCRIPTION
    Runs one bounded status sync, checks remote liveness, compares the latest
    status to the prior watcher snapshot, and writes
    runtime/reports/vbt_watcher_status.json. This script never starts, stops,
    restarts, kills, relaunches, or mutates the remote run.
#>

param(
    [string]$Repo = "C:\Users\MSI\repos\hft3",
    [string]$SshHost = "",
    [int]$SshPort = 0,
    [string]$RemoteRepo = "/root/hft3/repo",
    [string]$TmuxSession = "",
    [string]$RunId = "",
    [int]$ExpectedWorkUnits = 0,
    [int]$StaleAfterMinutes = 20,
    [int]$SyncTimeoutSeconds = 120,
    [int]$LivenessTimeoutSeconds = 30,
    [string]$StatusPath = "",
    [string]$WatcherStatusPath = "",
    [string]$PowerShellExe = "",
    [switch]$SkipSync,
    [switch]$SkipRemoteLiveness,
    [switch]$Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-RepoPath {
    param([string]$PathValue)
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return $PathValue
    }
    return (Join-Path $Repo $PathValue)
}

function ConvertTo-JsonFile {
    param(
        [string]$PathValue,
        [hashtable]$Payload
    )
    $parent = Split-Path -Parent $PathValue
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $Payload | ConvertTo-Json -Depth 8 | Set-Content -Path $PathValue -Encoding UTF8
}

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
        [string]$Name,
        [object]$DefaultValue = $null
    )
    if ($null -eq $ObjectValue) {
        return $DefaultValue
    }
    $prop = $ObjectValue.PSObject.Properties[$Name]
    if ($null -eq $prop -or $null -eq $prop.Value) {
        return $DefaultValue
    }
    if ($prop.Value -is [string] -and $prop.Value -eq "") {
        return $DefaultValue
    }
    return $prop.Value
}

function Format-UtcTimestampString {
    param([DateTimeOffset]$Value)
    return $Value.ToUniversalTime().ToString(
        "yyyy-MM-ddTHH:mm:ss.fffffff'Z'",
        [System.Globalization.CultureInfo]::InvariantCulture
    )
}

function ConvertTo-UtcTimestampString {
    param([object]$Value)
    if ($null -eq $Value) {
        return $null
    }
    if ($Value -is [string]) {
        if ($Value -eq "") {
            return $null
        }
        $parsedTimestamp = [DateTimeOffset]::MinValue
        if ([DateTimeOffset]::TryParse(
            $Value,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::AssumeUniversal,
            [ref]$parsedTimestamp
        )) {
            return Format-UtcTimestampString $parsedTimestamp
        }
        return $Value
    }
    if ($Value -is [DateTimeOffset]) {
        return Format-UtcTimestampString $Value
    }
    if ($Value -is [DateTime]) {
        return Format-UtcTimestampString ([DateTimeOffset]$Value.ToUniversalTime())
    }
    return [string]$Value
}

function Get-IntValue {
    param(
        [object]$Value,
        [int]$DefaultValue = 0
    )
    if ($null -eq $Value) {
        return $DefaultValue
    }
    if ($Value -is [string] -and $Value -eq "") {
        return $DefaultValue
    }
    try {
        return [int]$Value
    } catch {
        return $DefaultValue
    }
}

function Get-NullableIntValue {
    param([object]$Value)
    if ($null -eq $Value) {
        return $null
    }
    if ($Value -is [string] -and $Value -eq "") {
        return $null
    }
    try {
        return [int]$Value
    } catch {
        return $null
    }
}

function Get-NullableDoubleValue {
    param([object]$Value)
    if ($null -eq $Value) {
        return $null
    }
    if ($Value -is [string] -and $Value -eq "") {
        return $null
    }
    try {
        return [double]$Value
    } catch {
        return $null
    }
}

function Get-TimeValue {
    param([object]$Value)
    if ($null -eq $Value) {
        return $null
    }
    if ($Value -is [string] -and $Value -eq "") {
        return $null
    }
    try {
        return [DateTimeOffset]::Parse([string]$Value)
    } catch {
        return $null
    }
}

function Invoke-BoundedProcess {
    param(
        [string]$FilePath,
        [string]$Arguments,
        [int]$TimeoutSeconds,
        [hashtable]$Environment = @{}
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.Arguments = $Arguments
    $psi.WorkingDirectory = $Repo
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    foreach ($key in $Environment.Keys) {
        $psi.Environment[$key] = [string]$Environment[$key]
    }

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()
    $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
    $stderrTask = $proc.StandardError.ReadToEndAsync()
    $finished = $proc.WaitForExit($TimeoutSeconds * 1000)
    if (-not $finished) {
        try {
            $proc.Kill()
        } catch {
        }
        return [pscustomobject]@{
            exit_code = -1
            timed_out = $true
            stdout = ""
            stderr = "timed out after ${TimeoutSeconds}s"
        }
    }
    return [pscustomobject]@{
        exit_code = $proc.ExitCode
        timed_out = $false
        stdout = $stdoutTask.Result
        stderr = $stderrTask.Result
    }
}

function ConvertTo-PosixShellSingleQuoted {
    param([string]$Value)
    if ($null -eq $Value) { return "''" }
    return "'" + ($Value -replace "'", "'\''") + "'"
}

function Test-SafeHostIdentifier {
    param([string]$Value)
    if (-not $Value) { return $false }
    return $Value -match '^[A-Za-z0-9@._:-]+$'
}

function Invoke-RemoteCommand {
    param(
        [string]$RemoteCommand,
        [int]$TimeoutSeconds
    )
    if (-not (Test-SafeHostIdentifier $SshHost)) {
        throw "unsafe SshHost rejected: $SshHost"
    }
    $args = "-o ConnectTimeout=15 -p $SshPort $SshHost `"$RemoteCommand`""
    return Invoke-BoundedProcess -FilePath "ssh" -Arguments $args -TimeoutSeconds $TimeoutSeconds
}

function Read-RemoteManifestHeader {
    param(
        [string]$RemotePath,
        [int]$TimeoutSeconds
    )
    # NOTE: the v2 runner writes paid_screen_run_manifest.json non-atomically
    # (manifest_path.write_text, no temp+rename). A mid-write read will raise
    # json.JSONDecodeError on the remote python3 and return empty stdout,
    # which the watcher treats as "manifest header unavailable" -> sync
    # failure -> CRITICAL after staleness. This is a known transient cause
    # and is fixed by making the v2 runner write atomically (temp+os.replace).
    $escapedPath = ConvertTo-PosixShellSingleQuoted $RemotePath
    $remoteCommand = @"
test -f $escapedPath && python3 - $escapedPath <<'PY'
import json
import sys
with open(sys.argv[1], encoding='utf-8') as f:
    manifest = json.load(f)
keys = ('run_id', 'out_dir', 'status', 'workers', 'expected_work_units', 'completed_work_units', 'failed_work_units', 'skipped_work_units', 'units_per_hour', 'collected_batches', 'batches_collected', 'expected_batches')
payload = {key: manifest.get(key) for key in keys if key in manifest}
print(json.dumps(payload, separators=(',', ':')))
PY
"@
    return Invoke-RemoteCommand -RemoteCommand $remoteCommand -TimeoutSeconds $TimeoutSeconds
}

function Get-ManifestHeaderScalar {
    param(
        [string]$HeaderText,
        [string]$Name
    )
    try {
        $manifest = $HeaderText | ConvertFrom-Json
        return Get-JsonValue $manifest $Name $null
    } catch {
    }
    $pattern = '(?m)^\s*"' + [regex]::Escape($Name) + '"\s*:\s*(?<value>"(?:\\.|[^"\\])*"|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false)\s*,?\s*$'
    $match = [regex]::Match($HeaderText, $pattern)
    if (-not $match.Success) {
        return $null
    }
    $raw = $match.Groups["value"].Value.Trim()
    if ($raw -eq "null") {
        return $null
    }
    if ($raw.StartsWith('"') -and $raw.EndsWith('"')) {
        return ($raw.Substring(1, $raw.Length - 2) -replace '\\"', '"' -replace '\\\\', '\')
    }
    return $raw
}

function Get-ManifestHeaderRunId {
    param([string]$HeaderText)
    $directRunId = Get-ManifestHeaderScalar -HeaderText $HeaderText -Name "run_id"
    if ($directRunId) {
        return [string]$directRunId
    }
    $outDir = Get-ManifestHeaderScalar -HeaderText $HeaderText -Name "out_dir"
    if (-not $outDir) {
        return $null
    }
    $parts = ([string]$outDir).TrimEnd("/") -split "/"
    return $parts[$parts.Count - 1]
}

function Write-LocalStatusFromManifestHeader {
    param(
        [string]$HeaderText,
        [string]$ManifestPath,
        [string]$OutPath
    )
    try {
        if (-not $HeaderText) {
            throw "manifest header missing"
        }
        $manifestRunId = Get-ManifestHeaderRunId -HeaderText $HeaderText
        $runIdMismatch = $manifestRunId -and $manifestRunId -ne $RunId
        $runIdMissing = -not $manifestRunId
        $expected = Get-IntValue (Get-ManifestHeaderScalar -HeaderText $HeaderText -Name "expected_work_units") -1
        $expectedMismatch = $expected -ne $ExpectedWorkUnits
        $completed = Get-NullableIntValue (Get-ManifestHeaderScalar -HeaderText $HeaderText -Name "completed_work_units")
        $failed = Get-NullableIntValue (Get-ManifestHeaderScalar -HeaderText $HeaderText -Name "failed_work_units")
        $skipped = Get-NullableIntValue (Get-ManifestHeaderScalar -HeaderText $HeaderText -Name "skipped_work_units")
        $completedKnown = $null -ne $completed
        $failedKnown = $null -ne $failed
        $skippedKnown = $null -ne $skipped
        $failedCount = if ($failedKnown) { $failed } else { 0 }
        $skippedCount = if ($skippedKnown) { $skipped } else { 0 }
        $rate = Get-NullableDoubleValue (Get-ManifestHeaderScalar -HeaderText $HeaderText -Name "units_per_hour")
        $remaining = if ($expected -gt 0 -and $completedKnown -and $failedKnown -and $skippedKnown) {
            [Math]::Max(0, $expected - $completed - $failedCount - $skippedCount)
        } else {
            $null
        }
        $etaSeconds = $null
        $etaUtc = $null
        if ($remaining -ne $null -and $rate -ne $null -and $rate -gt 0) {
            $etaSeconds = [int]($remaining / $rate * 3600)
            $etaUtc = ConvertTo-UtcTimestampString ([DateTimeOffset]::UtcNow.AddSeconds($etaSeconds))
        }
        $manifestState = ([string](Get-ManifestHeaderScalar -HeaderText $HeaderText -Name "status")).ToLowerInvariant()
        $accounted = if ($completedKnown -and $failedKnown -and $skippedKnown) {
            $completed + $failedCount + $skippedCount
        } else {
            $null
        }
        if ($runIdMissing) {
            $statusState = "run_id_missing"
        } elseif ($runIdMismatch) {
            $statusState = "run_id_mismatch"
        } elseif ($expectedMismatch) {
            $statusState = "expected_count_mismatch"
        } elseif ($manifestState -eq "complete") {
            if (
                $expected -gt 0 -and
                $completedKnown -and
                $failedKnown -and
                $skippedKnown -and
                $failed -eq 0 -and
                $skipped -eq 0 -and
                $accounted -eq $expected
            ) {
                $statusState = "complete"
            } elseif ($failedCount -gt 0 -or $skippedCount -gt 0) {
                $statusState = "partial_failed"
            } else {
                $statusState = "complete_count_mismatch"
            }
        } elseif ($failedCount -gt 0 -or $skippedCount -gt 0) {
            $statusState = "partial_failed"
        } elseif ($manifestState) {
            $statusState = $manifestState
        } elseif (
            $expected -gt 0 -and
            $completedKnown -and
            $failedKnown -and
            $skippedKnown -and
            $failed -eq 0 -and
            $skipped -eq 0 -and
            $accounted -eq $expected
        ) {
            $statusState = "complete"
        } elseif ($completedKnown -and $completed -gt 0) {
            $statusState = "running"
        } else {
            $statusState = "observed"
        }
        $generated = ConvertTo-UtcTimestampString ([DateTimeOffset]::UtcNow)
        $collectedBatches = Get-NullableIntValue (Get-ManifestHeaderScalar -HeaderText $HeaderText -Name "collected_batches")
        if ($null -eq $collectedBatches) {
            $collectedBatches = Get-NullableIntValue (Get-ManifestHeaderScalar -HeaderText $HeaderText -Name "batches_collected")
        }
        $expectedBatches = Get-NullableIntValue (Get-ManifestHeaderScalar -HeaderText $HeaderText -Name "expected_batches")
        $anomalyList = @()
        if ($runIdMissing) {
            $anomalyList += "manifest header did not expose run_id or out_dir"
        }
        if ($runIdMismatch) {
            $anomalyList += "manifest run_id=$manifestRunId does not match configured watcher run_id=$RunId"
        }
        if ($expectedMismatch) {
            $anomalyList += "manifest expected_work_units=$expected does not match configured watcher expected_work_units=$ExpectedWorkUnits"
        }
        if ($manifestState -eq "complete" -and -not $completedKnown) {
            $anomalyList += "manifest status=complete but completed_work_units missing/unknown"
        }
        if ($manifestState -eq "complete" -and -not $failedKnown) {
            $anomalyList += "manifest status=complete but failed_work_units missing/unknown"
        }
        if ($manifestState -eq "complete" -and -not $skippedKnown) {
            $anomalyList += "manifest status=complete but skipped_work_units missing/unknown"
        }
        if ($statusState -eq "complete_count_mismatch") {
            $anomalyList += "manifest status=complete but accounted_work_units != expected_work_units"
        }
        if ($failedCount -gt 0) {
            $anomalyList += "failed_work_units=$failedCount"
        }
        if ($skippedCount -gt 0) {
            $anomalyList += "skipped_work_units=$skippedCount"
        }
        $hostLabel = Get-FirstMetadataValue -EnvName "VBT_HOST_LABEL" -Status $metadataStatus -StatusNames @("host_label", "host", "instance_label", "instance_type")
        $payload = @{
            state = $statusState
            status = $statusState
            run_id = if ($manifestRunId) { $manifestRunId } else { $RunId }
            configured_run_id = $RunId
            workers = Get-NullableIntValue (Get-ManifestHeaderScalar -HeaderText $HeaderText -Name "workers")
            expected = $expected
            completed = $completed
            failed = $failed
            skipped = $skipped
            expected_work_units = $expected
            completed_work_units = $completed
            failed_work_units = $failed
            skipped_work_units = $skipped
            collected_batches = $collectedBatches
            expected_batches = $expectedBatches
            units_per_hour = $rate
            eta_seconds = $etaSeconds
            eta_utc = ConvertTo-UtcTimestampString $etaUtc
            last_sync_utc = ConvertTo-UtcTimestampString $generated
            generated_at_utc = ConvertTo-UtcTimestampString $generated
            manifest_artifact = if ($manifestRunId) { "research_cards/pipeline_runs/$manifestRunId/paid_screen_run_manifest.json" } else { "research_cards/pipeline_runs/$RunId/paid_screen_run_manifest.json" }
            manifest_path = $ManifestPath
            manifest_sync_mode = "remote_top_level_json"
            artifact = "research_cards/pipeline_runs/$RunId"
            output_path = "research_cards/pipeline_runs/$RunId"
            progress = @{
                expected = $expected
                total = $expected
                completed = $completed
                failed = $failed
                skipped = $skipped
                remaining = $remaining
                collected_batches = $collectedBatches
                expected_batches = $expectedBatches
            }
            anomalies = if ($anomalyList.Count -gt 0) { $anomalyList } else { $null }
            host_label = $hostLabel
            ssh_host = $SshHost
            ssh_port = $SshPort
            tmux_session = $TmuxSession
        }
        ConvertTo-JsonFile -PathValue $OutPath -Payload $payload
        return [pscustomobject]@{ exit_code = 0; timed_out = $false; stderr = ""; stdout = "local status built" }
    } catch {
        return [pscustomobject]@{ exit_code = 1; timed_out = $false; stderr = $_.Exception.Message; stdout = "" }
    }
}

function Write-LocalFastStatusAudit {
    param(
        [object]$Status,
        [string]$OutPath
    )
    try {
        if ($null -eq $Status) {
            throw "status missing"
        }
        $expected = Get-IntValue (Get-JsonValue $Status "expected_work_units" (Get-JsonValue $Status "expected" $ExpectedWorkUnits)) $ExpectedWorkUnits
        $completed = Get-IntValue (Get-JsonValue $Status "completed_work_units" (Get-JsonValue $Status "completed" 0)) 0
        $failed = Get-NullableIntValue (Get-JsonValue $Status "failed_work_units" (Get-JsonValue $Status "failed"))
        $skipped = Get-NullableIntValue (Get-JsonValue $Status "skipped_work_units" (Get-JsonValue $Status "skipped"))
        $failedCount = Get-IntValue $failed 0
        $skippedCount = Get-IntValue $skipped 0
        $accounted = $completed + $failedCount + $skippedCount
        $remaining = if ($expected -gt 0) { [Math]::Max(0, $expected - $accounted) } else { $null }
        $latestRun = @{
            run_id = [string](Get-JsonValue $Status "run_id" $RunId)
            manifest_path = Get-JsonValue $Status "manifest_path" $null
            manifest_artifact = Get-JsonValue $Status "manifest_artifact" $null
            manifest_status = [string](Get-JsonValue $Status "status" (Get-JsonValue $Status "state" "unknown"))
            artifact = Get-JsonValue $Status "artifact" $null
            workers = Get-NullableIntValue (Get-JsonValue $Status "workers")
            expected_work_units = $expected
            completed_work_units = $completed
            failed_work_units = $failed
            skipped_work_units = $skipped
            collected_batches = Get-NullableIntValue (Get-JsonValue $Status "collected_batches")
            expected_batches = Get-NullableIntValue (Get-JsonValue $Status "expected_batches")
            artifact_files_on_disk = $completed
            done_units_estimate = $accounted
            remaining_units_estimate = $remaining
            units_per_hour = Get-NullableDoubleValue (Get-JsonValue $Status "units_per_hour")
            eta_seconds = Get-NullableIntValue (Get-JsonValue $Status "eta_seconds")
            promoted_ids_in_artifacts = $null
            unique_feature_set_ids = 0
            feature_set_id_counts = @{}
            duplicate_feature_set_ids = @{}
            feature_plane_status_counts = @{}
            production_feature_set_ok = $null
            feature_plane_production_ok = $null
            validation_errors = @()
            validation_error_count = 0
            audit_mode = "fast_status"
            artifact_audit_skipped = $true
        }
        $payload = @{
            generated_at_utc = ConvertTo-UtcTimestampString ([DateTimeOffset]::UtcNow)
            runs_scanned = 1
            audit_mode = "fast_status"
            latest_run = $latestRun
            all_runs = @($latestRun)
        }
        ConvertTo-JsonFile -PathValue $OutPath -Payload $payload
        return [pscustomobject]@{ exit_code = 0; timed_out = $false; stderr = ""; stdout = "local fast_status audit built" }
    } catch {
        return [pscustomobject]@{ exit_code = 1; timed_out = $false; stderr = $_.Exception.Message; stdout = "" }
    }
}

function Add-Reason {
    param(
        [System.Collections.Generic.List[string]]$Reasons,
        [string]$Reason
    )
    if ($Reason) {
        $Reasons.Add($Reason) | Out-Null
    }
}

function Get-FirstMetadataValue {
    param(
        [string]$EnvName,
        [object]$Status,
        [string[]]$StatusNames
    )
    $envValue = [Environment]::GetEnvironmentVariable($EnvName)
    if ($envValue) {
        return $envValue
    }
    foreach ($statusName in $StatusNames) {
        $statusValue = Get-JsonValue $Status $statusName $null
        if ($statusValue) {
            return $statusValue
        }
    }
    return $null
}

function Add-MissingMetadata {
    param(
        [System.Collections.Generic.List[string]]$Missing,
        [string]$Name
    )
    if (-not $Missing.Contains($Name)) {
        $Missing.Add($Name) | Out-Null
    }
}

if (-not (Test-Path $Repo)) {
    throw "canonical repo not found: $Repo"
}

if ($PowerShellExe -eq "") {
    $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($pwshCmd) {
        $PowerShellExe = $pwshCmd.Source
    } else {
        $PowerShellExe = "powershell.exe"
    }
}

$statusPathResolved = if ($StatusPath) { Resolve-RepoPath $StatusPath } else { Join-Path $Repo "runtime\reports\vbt_full_status.json" }
$watcherPathResolved = if ($WatcherStatusPath) { Resolve-RepoPath $WatcherStatusPath } else { Join-Path $Repo "runtime\reports\vbt_watcher_status.json" }
$auditPathResolved = Join-Path $Repo "runtime\reports\vbt_run_progress_audit.json"
$startedAt = [DateTimeOffset]::UtcNow
$reasons = New-Object System.Collections.Generic.List[string]
$syncOk = $false
$sshOk = $false
$tmuxOk = $false
$processOk = $false
$syncExitCode = $null

$metadataStatus = $null
try {
    $metadataStatus = Read-JsonFile $statusPathResolved
} catch {
    Add-Reason $reasons "status metadata parse failed during configuration: $($_.Exception.Message)"
}

if (-not $RunId) {
    $RunId = [string](Get-FirstMetadataValue -EnvName "VBT_RUN_ID" -Status $metadataStatus -StatusNames @("run_id", "configured_run_id"))
}
if ($ExpectedWorkUnits -le 0) {
    $expectedValue = Get-FirstMetadataValue -EnvName "VBT_EXPECTED_WORK_UNITS" -Status $metadataStatus -StatusNames @("expected_work_units", "expected")
    $ExpectedWorkUnits = Get-IntValue $expectedValue 0
}
if (-not $SshHost) {
    $SshHost = [string](Get-FirstMetadataValue -EnvName "VAST_SSH_HOST" -Status $metadataStatus -StatusNames @("ssh_host"))
}
if ($SshPort -le 0) {
    $portValue = Get-FirstMetadataValue -EnvName "VAST_SSH_PORT" -Status $metadataStatus -StatusNames @("ssh_port")
    $SshPort = Get-IntValue $portValue 0
}
if (-not $TmuxSession) {
    $TmuxSession = [string](Get-FirstMetadataValue -EnvName "VBT_TMUX_SESSION" -Status $metadataStatus -StatusNames @("tmux_session"))
}

$missingMetadata = New-Object System.Collections.Generic.List[string]
if (-not $RunId) {
    Add-MissingMetadata $missingMetadata "RunId (-RunId, VBT_RUN_ID, or status run_id)"
}
if ($ExpectedWorkUnits -le 0) {
    Add-MissingMetadata $missingMetadata "ExpectedWorkUnits (-ExpectedWorkUnits, VBT_EXPECTED_WORK_UNITS, or status expected_work_units)"
}
if (-not $SkipSync -or -not $SkipRemoteLiveness) {
    if (-not $SshHost) {
        Add-MissingMetadata $missingMetadata "SshHost (-SshHost, VAST_SSH_HOST, or status ssh_host)"
    }
    if ($SshPort -le 0) {
        Add-MissingMetadata $missingMetadata "SshPort (-SshPort, VAST_SSH_PORT, or status ssh_port)"
    }
}
if (-not $SkipRemoteLiveness -and -not $TmuxSession) {
    Add-MissingMetadata $missingMetadata "TmuxSession (-TmuxSession, VBT_TMUX_SESSION, or status tmux_session)"
}
if ($missingMetadata.Count -gt 0) {
    throw "missing required watcher metadata: $($missingMetadata -join '; ')"
}

$remoteManifestPath = "$RemoteRepo/research_cards/pipeline_runs/$RunId/paid_screen_run_manifest.json"

if (-not $SkipSync) {
    $manifestHeader = Read-RemoteManifestHeader -RemotePath $remoteManifestPath -TimeoutSeconds $SyncTimeoutSeconds
    if ($manifestHeader.timed_out -or $manifestHeader.exit_code -ne 0 -or -not $manifestHeader.stdout) {
        Add-Reason $reasons "remote manifest header unavailable for configured run_id=$RunId"
        $syncExitCode = $manifestHeader.exit_code
    } else {
        $statusBuild = Write-LocalStatusFromManifestHeader -HeaderText $manifestHeader.stdout -ManifestPath $remoteManifestPath -OutPath $statusPathResolved
        $auditBuild = if (-not $statusBuild.timed_out -and $statusBuild.exit_code -eq 0) {
            Write-LocalFastStatusAudit -Status (Read-JsonFile $statusPathResolved) -OutPath $auditPathResolved
        } else {
            [pscustomobject]@{ exit_code = 1; timed_out = $false; stderr = "status build failed"; stdout = "" }
        }
        $syncExitCode = $statusBuild.exit_code
        $syncOk = (
            -not $statusBuild.timed_out -and $statusBuild.exit_code -eq 0 -and
            -not $auditBuild.timed_out -and $auditBuild.exit_code -eq 0
        )
        if (-not $syncOk) {
            Add-Reason $reasons "sync failed manifest_header=$($manifestHeader.exit_code) local_status_build=$($statusBuild.exit_code) local_audit_build=$($auditBuild.exit_code)"
        }
    }
} else {
    $syncOk = $true
    Add-Reason $reasons "sync skipped by operator"
}

if (-not $SkipRemoteLiveness) {
    $escapedTmux = ConvertTo-PosixShellSingleQuoted $TmuxSession
    $tmuxResult = Invoke-RemoteCommand -RemoteCommand "tmux has-session -t $escapedTmux" -TimeoutSeconds $LivenessTimeoutSeconds
    $sshOk = (-not $tmuxResult.timed_out -and $tmuxResult.exit_code -ne 255)
    $tmuxOk = ($sshOk -and $tmuxResult.exit_code -eq 0)
    if (-not $tmuxOk) {
        $pgrepResult = Invoke-RemoteCommand -RemoteCommand "pgrep -af 'run_vectorbt_paid_screen(_v2)?[.]py'" -TimeoutSeconds $LivenessTimeoutSeconds
        $sshOk = $sshOk -or (-not $pgrepResult.timed_out -and $pgrepResult.exit_code -ne 255)
        $processOk = (
            -not $pgrepResult.timed_out -and
            $pgrepResult.exit_code -eq 0 -and
            $pgrepResult.stdout.Contains($RunId)
        )
    }
} else {
    $sshOk = $true
    $tmuxOk = $true
    $processOk = $true
    Add-Reason $reasons "remote liveness skipped by operator"
}

$previous = Read-JsonFile $watcherPathResolved
$status = $null
$statusParseOk = $true
try {
    $status = Read-JsonFile $statusPathResolved
} catch {
    $statusParseOk = $false
    Add-Reason $reasons "status JSON parse failed: $($_.Exception.Message)"
}

if ($null -eq $status) {
    $statusParseOk = $false
    Add-Reason $reasons "status JSON missing: $statusPathResolved"
}

$state = "unknown"
$completed = $null
$expected = $ExpectedWorkUnits
$failed = $null
$skipped = $null
$unitsPerHour = $null
$etaUtc = $null
$lastSyncUtc = $null
$statusAgeSeconds = $null
$accounted = $null
$stallCount = 0
$sshFailureCount = 0

if ($statusParseOk) {
    $state = [string](Get-JsonValue $status "state" (Get-JsonValue $status "status" "unknown"))
    $statusRunId = [string](Get-JsonValue $status "run_id" "")
    if ($statusRunId -and $statusRunId -ne $RunId) {
        $statusParseOk = $false
        Add-Reason $reasons "status run_id=$statusRunId does not match configured run_id=$RunId"
    }
    $completed = Get-NullableIntValue (Get-JsonValue $status "completed_work_units" (Get-JsonValue $status "completed"))
    $expected = Get-IntValue (Get-JsonValue $status "expected_work_units" (Get-JsonValue $status "expected" $ExpectedWorkUnits)) $ExpectedWorkUnits
    if ($expected -ne $ExpectedWorkUnits) {
        $statusParseOk = $false
        Add-Reason $reasons "status expected_work_units=$expected does not match configured expected_work_units=$ExpectedWorkUnits"
    }
    $failed = Get-NullableIntValue (Get-JsonValue $status "failed_work_units" (Get-JsonValue $status "failed"))
    $skipped = Get-NullableIntValue (Get-JsonValue $status "skipped_work_units" (Get-JsonValue $status "skipped"))
    $unitsPerHour = Get-NullableDoubleValue (Get-JsonValue $status "units_per_hour")
    $etaUtc = ConvertTo-UtcTimestampString (Get-JsonValue $status "eta_utc" $null)
    $lastSyncUtc = ConvertTo-UtcTimestampString (Get-JsonValue $status "last_sync_utc" (Get-JsonValue $status "generated_at_utc" $null))
    $lastSyncTime = Get-TimeValue $lastSyncUtc
    if ($null -ne $lastSyncTime) {
        $statusAgeSeconds = [int][Math]::Max(0, ([DateTimeOffset]::UtcNow - $lastSyncTime).TotalSeconds)
    }
    $accounted = (Get-IntValue $completed 0) + (Get-IntValue $failed 0) + (Get-IntValue $skipped 0)
}

if ($null -ne $previous -and [string](Get-JsonValue $previous "run_id" "") -eq $RunId) {
    $prevCompleted = Get-NullableIntValue (Get-JsonValue $previous "completed_work_units")
    $prevStallCount = Get-IntValue (Get-JsonValue $previous "stall_count" 0) 0
    if ($state -eq "running" -and $null -ne $completed -and $null -ne $prevCompleted -and $completed -le $prevCompleted) {
        $stallCount = $prevStallCount + 1
    }
    $prevSshFailures = Get-IntValue (Get-JsonValue $previous "ssh_failure_count" 0) 0
    if (-not $sshOk) {
        $sshFailureCount = $prevSshFailures + 1
    }
} elseif (-not $sshOk) {
    $sshFailureCount = 1
}

$verdict = "ALIVE"
if (-not $statusParseOk) {
    $verdict = "CRITICAL"
} elseif ($state -eq "complete" -and $expected -gt 0 -and $completed -eq $expected -and $null -ne $failed -and $null -ne $skipped -and $failed -eq 0 -and $skipped -eq 0) {
    $verdict = "COMPLETE"
    Add-Reason $reasons "complete and completed units match expected with no failed/skipped units"
} elseif ($state -eq "complete" -or $state -eq "complete_count_mismatch" -or $state -eq "expected_count_mismatch") {
    $verdict = "CRITICAL"
    Add-Reason $reasons "terminal status is not clean: completed=$completed failed=$failed skipped=$skipped accounted=$accounted expected=$expected"
} elseif ($statusAgeSeconds -ne $null -and $statusAgeSeconds -gt ($StaleAfterMinutes * 60)) {
    $verdict = "CRITICAL"
    Add-Reason $reasons "status age ${statusAgeSeconds}s exceeds stale threshold"
} elseif ($sshFailureCount -ge 2) {
    $verdict = "CRITICAL"
    Add-Reason $reasons "SSH failed across $sshFailureCount watcher checks"
} elseif ($sshOk -and -not ($tmuxOk -or $processOk) -and $state -ne "complete") {
    $verdict = "CRITICAL"
    Add-Reason $reasons "tmux session and process fallback absent"
} elseif ($state -eq "running" -and $stallCount -ge 2) {
    $verdict = "WARN_STALLED"
    Add-Reason $reasons "completed units did not increase across $stallCount consecutive checks"
} elseif (-not $sshOk) {
    $verdict = "ALIVE"
    Add-Reason $reasons "single SSH failure; waiting for second check before CRITICAL"
} elseif ($state -ne "running" -and $state -ne "complete") {
    $verdict = "CRITICAL"
    Add-Reason $reasons "unexpected state: $state"
} else {
    Add-Reason $reasons "running and remote liveness present"
}

$payload = @{
    checked_at_utc = ConvertTo-UtcTimestampString ([DateTimeOffset]::UtcNow)
    started_at_utc = ConvertTo-UtcTimestampString $startedAt
    run_id = $RunId
    state = $state
    completed_work_units = $completed
    expected_work_units = $expected
    failed_work_units = $failed
    skipped_work_units = $skipped
    units_per_hour = $unitsPerHour
    eta_utc = ConvertTo-UtcTimestampString $etaUtc
    last_sync_utc = ConvertTo-UtcTimestampString $lastSyncUtc
    ssh_ok = $sshOk
    tmux_ok = $tmuxOk
    process_ok = $processOk
    sync_ok = $syncOk
    sync_exit_code = $syncExitCode
    status_age_seconds = $statusAgeSeconds
    stale_after_seconds = $StaleAfterMinutes * 60
    ssh_host = $SshHost
    ssh_port = $SshPort
    tmux_session = $TmuxSession
    stall_count = $stallCount
    ssh_failure_count = $sshFailureCount
    verdict = $verdict
    reason = (($reasons | Select-Object -Unique) -join "; ")
    next_check_hint = if ($verdict -eq "COMPLETE") { "terminal; run final artifact audit before promotion claims" } elseif ($verdict -eq "CRITICAL") { "inspect Vast tmux/logs before spending more compute" } else { "next scheduled watcher check" }
    status_artifact = "runtime/reports/vbt_full_status.json"
    audit_artifact = "runtime/reports/vbt_run_progress_audit.json"
}

ConvertTo-JsonFile -PathValue $watcherPathResolved -Payload $payload

if (-not $Quiet) {
    Write-Host ("{0}: run={1} state={2} completed={3}/{4} ssh={5} tmux={6} proc={7} eta={8}" -f `
        $verdict, $RunId, $state, ($completed -as [string]), $expected, $sshOk, $tmuxOk, $processOk, $etaUtc)
    if ($payload.reason) {
        Write-Host ("reason: {0}" -f $payload.reason)
    }
    Write-Host ("wrote: {0}" -f $watcherPathResolved)
}

if ($verdict -eq "CRITICAL") {
    exit 2
}
if ($verdict -eq "WARN_STALLED") {
    exit 1
}
exit 0
