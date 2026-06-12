#Requires -Version 5.1
<#
.SYNOPSIS
    HFT3 slow-tier nightly pipeline: pull manifests, run F1 labeler, run F2 brief.

.DESCRIPTION
    Runs after the CME trading session closes.  The script is designed to be
    invoked at approximately 17:35 America/Chicago (05:35 the following day
    local time, UTC+7).

    DATE LOGIC:
    The CME regular-trading-hours session ends at 16:00 CT and the post-session
    (extended) period ends at 17:00 CT.  When this script fires at 17:35 CT,
    the session that just ended is identified by TODAY's date in Chicago time,
    not tomorrow's.  We use Get-Date (local clock, UTC+7) and subtract 7+5=12
    hours to approximate Chicago civil time, then take the date portion.

    DST caveat: Chicago observes CDT (UTC-5) in summer and CST (UTC-6) in
    winter.  This script uses a fixed UTC-6 offset (CST) for date derivation.
    During CDT the computed date will be off by one hour but the date itself
    will still be correct because the run is at 17:35 CT, well inside the
    trading day boundary.  If precise DST handling becomes necessary, replace
    the offset arithmetic with a proper TimeZoneInfo conversion.

    STEPS:
    1. Compute the Chicago trade date (see DATE LOGIC above).
    2. SCP per-symbol manifests from CHI404 into the local manifest tree.
    3. Run `python -m llm_slow_tier nightly-label --date $date`.
    4. Run `python -m llm_slow_tier morning-brief --date $date`.
    5. Run `python -m llm_slow_tier status` (problem-only health check).
       If problems are detected, copy problems_latest.json to
       runtime/slow_tier/ATTENTION_{DATE}.json for glanceable detection.
    6. Log all output to runtime/slow_tier/nightly_{DATE}.log.
    7. Non-zero exits are logged but do NOT abort subsequent steps.

    CREDENTIALS: never echoed.  SSH key must be configured for the chi404
    user in the calling shell's ssh-agent or ~/.ssh/config.

.PARAMETER Date
    Override the computed trade date (YYYY-MM-DD).  Useful for manual
    backfill runs.

.PARAMETER Chi404Host
    SSH host string for CHI404 (default: chi404).
    Reads CHI404_HOST env var if set.

.PARAMETER DryRun
    Print what would be done without executing SCP or Python commands.
#>

param(
    [string]$Date = "",
    [string]$Chi404Host = ($env:CHI404_HOST ?? "chi404"),
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"   # non-zero exits are logged, not fatal

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
$RepoRoot  = Split-Path -Parent $PSScriptRoot
$LogDir    = Join-Path $RepoRoot "runtime\slow_tier"
$ArtRoot   = Join-Path $RepoRoot "artifacts\research_cards\slow_tier"
$ManifDir  = Join-Path $ArtRoot "manifests"

# PYTHONPATH — shims first so import stubs shadow real packages in test mode;
# then repo root (for llm_slow_tier as a package), packages, apps.
$env:PYTHONPATH = "C:\Users\MSI\.claude\shims;$RepoRoot;$RepoRoot\packages;$RepoRoot\apps"

# ---------------------------------------------------------------------------
# Date computation
# ---------------------------------------------------------------------------
if ($Date -eq "") {
    # Derive the Chicago trade date from UTC.
    # Chicago Standard Time (CST) = UTC-6.  See DST caveat in .DESCRIPTION.
    $utcNow  = [DateTime]::UtcNow
    $ctOffset = [TimeSpan]::FromHours(-6)   # CST; CDT would be -5
    $ctNow   = $utcNow + $ctOffset
    $Date    = $ctNow.ToString("yyyy-MM-dd")
}

# ---------------------------------------------------------------------------
# Log file
# ---------------------------------------------------------------------------
$null = New-Item -ItemType Directory -Force -Path $LogDir
$LogFile = Join-Path $LogDir "nightly_$Date.log"

function Write-Log {
    param([string]$Message)
    $ts = [DateTime]::UtcNow.ToString("o")
    $line = "$ts  $Message"
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

Write-Log "=== slow_tier_nightly START  date=$Date  dry_run=$DryRun ==="

# ---------------------------------------------------------------------------
# Step 1: Pull manifests from CHI404
# ---------------------------------------------------------------------------
# Remote path convention on CHI404:
#   /root/hft3/data/capture/{SYM}/{SYM}_{DATE}.manifest.json
# We pull the manifest files for all symbols for the given date using a
# glob pattern.  The local destination is:
#   artifacts/research_cards/slow_tier/manifests/{DATE}/
# ---------------------------------------------------------------------------

$LocalManifDir = Join-Path $ManifDir $Date
if (-not $DryRun) {
    $null = New-Item -ItemType Directory -Force -Path $LocalManifDir
}

$RemoteGlob = "/root/hft3/data/capture/*/*_${Date}.manifest.json"
$ScpCmd = "scp `"${Chi404Host}:${RemoteGlob}`" `"${LocalManifDir}\`""

Write-Log "SCP: $ScpCmd"
if (-not $DryRun) {
    & scp $Chi404Host`:$RemoteGlob "$LocalManifDir\" 2>&1 | ForEach-Object { Write-Log "  scp: $_" }
    if ($LASTEXITCODE -ne 0) {
        Write-Log "WARNING: scp exited $LASTEXITCODE — manifests may be incomplete"
    }
} else {
    Write-Log "  [DRY RUN] would scp $RemoteGlob -> $LocalManifDir"
}

# ---------------------------------------------------------------------------
# Step 2: Run nightly-label (F1)
# ---------------------------------------------------------------------------
Write-Log "--- nightly-label ---"
$LabelCmd = "python -m llm_slow_tier nightly-label --date $Date"
Write-Log "CMD: $LabelCmd"
if (-not $DryRun) {
    & python -m llm_slow_tier nightly-label --date $Date 2>&1 |
        ForEach-Object { Write-Log "  label: $_" }
    if ($LASTEXITCODE -ne 0) {
        Write-Log "WARNING: nightly-label exited $LASTEXITCODE (continuing)"
    }
} else {
    Write-Log "  [DRY RUN] would run: $LabelCmd"
}

# ---------------------------------------------------------------------------
# Step 3: Run morning-brief (F2)
# ---------------------------------------------------------------------------
Write-Log "--- morning-brief ---"
$BriefCmd = "python -m llm_slow_tier morning-brief --date $Date"
Write-Log "CMD: $BriefCmd"
if (-not $DryRun) {
    & python -m llm_slow_tier morning-brief --date $Date 2>&1 |
        ForEach-Object { Write-Log "  brief: $_" }
    if ($LASTEXITCODE -ne 0) {
        Write-Log "WARNING: morning-brief exited $LASTEXITCODE (continuing)"
    }
} else {
    Write-Log "  [DRY RUN] would run: $BriefCmd"
}

# ---------------------------------------------------------------------------
# Step 4: Run status health check
# ---------------------------------------------------------------------------
Write-Log "--- status ---"
$StatusCmd = "python -m llm_slow_tier status"
Write-Log "CMD: $StatusCmd"
if (-not $DryRun) {
    $StatusOutput = & python -m llm_slow_tier status 2>&1
    $StatusExit = $LASTEXITCODE
    $StatusOutput | ForEach-Object { Write-Log "  status: $_" }
    if ($StatusExit -ne 0) {
        Write-Log "PROBLEMS DETECTED — see runtime/slow_tier/problems_latest.json"
        # Copy problems_latest.json to a date-stamped ATTENTION file
        $ProblemsLatest = Join-Path $LogDir "problems_latest.json"
        $AttentionFile  = Join-Path $LogDir "ATTENTION_${Date}.json"
        if (Test-Path $ProblemsLatest) {
            Copy-Item -Path $ProblemsLatest -Destination $AttentionFile -Force
            Write-Log "Copied problems_latest.json -> ATTENTION_${Date}.json"
        }
    }
} else {
    Write-Log "  [DRY RUN] would run: $StatusCmd"
}

Write-Log "=== slow_tier_nightly END  date=$Date ==="
