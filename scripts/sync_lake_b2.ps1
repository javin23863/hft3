# Sync the local hft3 data lake to the B2 archive bucket (Hft3repo).
# Idempotent: rclone copy skips files already present with matching size/sha1.
# Usage: pwsh scripts\sync_lake_b2.ps1 [-Stage all|primaries|derived]
param(
    [ValidateSet("all", "primaries", "derived")]
    [string]$Stage = "all"
)

$ErrorActionPreference = "Continue"
$rc = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Rclone.Rclone_Microsoft.Winget.Source_8wekyb3d8bbwe\rclone-v1.74.3-windows-amd64\rclone.exe"
if (-not (Test-Path $rc)) { $rc = (Get-Command rclone -ErrorAction Stop).Source }

$lake   = "C:\hft3-lake"
$repo   = "C:\Users\MSI\repos\hft3"
$remote = "hft3-b2:Hft3repo"
$log    = Join-Path $repo "runtime\b2_sync.log"
$sw     = [Diagnostics.Stopwatch]::StartNew()

function Step([string]$name, [scriptblock]$body) {
    Add-Content $log "$(Get-Date -Format o) START $name"
    & $body
    Add-Content $log "$(Get-Date -Format o) DONE  $name rc=$LASTEXITCODE elapsed=$($sw.Elapsed)"
}

if ($Stage -in @("all", "primaries")) {
    Step "ledgers" {
        & $rc copy $lake "$remote/lake" --include "manifest.parquet" --include "manifest.pre_merge_*.parquet" --max-depth 1 --log-file $log --log-level INFO
        & $rc copy "$repo\runtime\databento" "$remote/ledgers/receipts" --log-file $log --log-level INFO
        & $rc copy "$lake\features\feature_manifest.jsonl" "$remote/ledgers/" --log-file $log --log-level INFO
    }
    Step "mbo_release (no events.jsonl)" {
        & $rc copy "$lake\mbo_release" "$remote/lake/mbo_release" --exclude "events.jsonl" --transfers 16 --checkers 32 --log-file $log --log-level ERROR
    }
    Step "raw" {
        & $rc copy "$lake\raw" "$remote/lake/raw" --transfers 8 --checkers 16 --log-file $log --log-level ERROR
    }
    Step "npz (no nested dup)" {
        & $rc copy "$lake\npz" "$remote/lake/npz" --exclude "/npz/**" --transfers 16 --checkers 32 --log-file $log --log-level ERROR
    }
    Step "kept event_tape raws" {
        & $rc copy "$repo\data\raw\event_tape" "$remote/lake/raw/event_tape" --transfers 8 --log-file $log --log-level ERROR
    }
}

if ($Stage -in @("all", "derived")) {
    Step "features" {
        & $rc copy "$lake\features" "$remote/lake/features" --transfers 16 --checkers 32 --log-file $log --log-level ERROR
    }
    foreach ($lane in @("crypto", "equities", "options", "vix_options", "sensors", "replay", "normalized")) {
        Step "lane $lane" {
            & $rc copy "$lake\$lane" "$remote/lake/$lane" --transfers 8 --log-file $log --log-level ERROR
        }
    }
}

Add-Content $log "$(Get-Date -Format o) SYNC COMPLETE stage=$Stage total=$($sw.Elapsed)"
