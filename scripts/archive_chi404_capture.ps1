# Pull-based Rithmic capture archival (runs on the workstation, nightly).
#
# CHI404 keeps ZERO cloud credentials. This job:
#   1. pulls closed-trade-date .cap files (+ manifests) from CHI404 via the
#      chi404-sftp rclone remote into a local staging dir
#   2. zstd-compresses each .cap
#   3. uploads .cap.zst + manifest to b2 Hft3repo/capture/rithmic/{CONTRACT}/
#   4. verifies remote size, then clears staging
#   5. prunes .cap files on CHI404 older than RETAIN_DAYS (default 30) —
#      only those whose .zst is confirmed present in B2
#
# Idempotent: B2-present files are skipped; staging is rebuilt each run.
param(
    [int]$RetainDays = 30
)
$ErrorActionPreference = "Continue"
$rc = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Rclone.Rclone_Microsoft.Winget.Source_8wekyb3d8bbwe\rclone-v1.74.3-windows-amd64\rclone.exe"
$zstd = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Meta.Zstandard_Microsoft.Winget.Source_8wekyb3d8bbwe\zstd-v1.5.7-win64\zstd.exe"
$src = "chi404-sftp:/root/hft3/data/capture"
$dest = "hft3-b2:Hft3repo/capture/rithmic"
$staging = "C:\hft3-lake\capture_staging"
$log = "C:\Users\MSI\repos\hft3\runtime\capture_archive.log"

function Log([string]$m) { Add-Content $log "$(Get-Date -Format o) $m" }

# Current CME trade date (Chicago local; after 17:00 CT the trade date rolls)
$chi = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow, "Central Standard Time")
$tradeDate = $chi.Date
if ($chi.Hour -ge 17) { $tradeDate = $tradeDate.AddDays(1) }
$cutoff = $tradeDate.AddDays(-$RetainDays)
Log "run start tradeDate=$($tradeDate.ToString('yyyy-MM-dd')) retain=${RetainDays}d"

# Inventory remote .cap files
$listing = & $rc lsjson $src --recursive 2>>$log | ConvertFrom-Json
$caps = $listing | Where-Object { -not $_.IsDir -and $_.Name -match '_(\d{4}-\d{2}-\d{2})\.cap$' }
$archived = 0; $pruned = 0; $skipped = 0; $failed = 0
New-Item -ItemType Directory -Force $staging | Out-Null

foreach ($f in $caps) {
    $dateStr = [regex]::Match($f.Name, '_(\d{4}-\d{2}-\d{2})\.cap$').Groups[1].Value
    $fdate = [DateTime]::ParseExact($dateStr, 'yyyy-MM-dd', $null)
    if ($fdate -ge $tradeDate) { $skipped++; continue }   # still open, never touch

    $contract = Split-Path $f.Path -Parent
    $zstName = "$($f.Name).zst"
    $remoteZst = "$dest/$contract/$zstName"

    # already archived? (size check below still prunes if eligible)
    $have = & $rc lsjson $remoteZst 2>$null | ConvertFrom-Json
    if (-not $have) {
        $localCap = Join-Path $staging $f.Name
        & $rc copyto "$src/$($f.Path)" $localCap 2>>$log
        if (-not (Test-Path $localCap)) { Log "PULL FAIL $($f.Path)"; $failed++; continue }
        & $zstd -q -T0 -10 -f $localCap -o "$localCap.zst" 2>>$log
        & $rc copyto "$localCap.zst" $remoteZst 2>>$log
        $have = & $rc lsjson $remoteZst 2>$null | ConvertFrom-Json
        if (-not $have -or $have[0].Size -ne (Get-Item "$localCap.zst").Length) {
            Log "VERIFY FAIL $remoteZst"; $failed++; Remove-Item $localCap, "$localCap.zst" -Force -ErrorAction SilentlyContinue; continue
        }
        # manifest rides along
        $man = $f.Path -replace '\.cap$', '.manifest.json'
        & $rc copyto "$src/$man" "$dest/$contract/$(Split-Path $man -Leaf)" 2>$null
        Remove-Item $localCap, "$localCap.zst" -Force -ErrorAction SilentlyContinue
        $archived++
        Log "archived $($f.Path) -> $remoteZst"
    }

    # prune on CHI404 once past retention AND confirmed in B2
    if ($fdate -lt $cutoff -and $have) {
        & $rc deletefile "$src/$($f.Path)" 2>>$log
        $pruned++
        Log "pruned remote $($f.Path) (age > ${RetainDays}d, B2-confirmed)"
    }
}
Log "run done archived=$archived pruned=$pruned skipped=$skipped failed=$failed"
exit ($failed -gt 0 ? 1 : 0)
