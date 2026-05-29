# Push R|Trader log files from this Windows machine to CHI404 watch dir.
# R|Trader runs natively on Windows; CHI404 capture reads /root/hft3/rtrader_watch.
param(
    [string]$Chi404Host = "chi404",
    [string]$RemoteWatch = "/root/hft3/rtrader_watch",
    [int]$IntervalSec = 2
)

$ErrorActionPreference = "SilentlyContinue"
$sources = @(
    "$env:USERPROFILE\Documents\Rithmic",
    "${env:ProgramFiles(x86)}\Rithmic"
)

ssh $Chi404Host "mkdir -p $RemoteWatch" | Out-Null

while ($true) {
    foreach ($src in $sources) {
        if (-not (Test-Path $src)) { continue }
        Get-ChildItem -Path $src -Recurse -Include *.log,*.txt,*.csv,*.ndjson -File -ErrorAction SilentlyContinue |
            ForEach-Object {
                $rel = $_.FullName.Substring($src.Length).TrimStart('\').Replace('\', '/')
                $dest = "${RemoteWatch}/${rel}"
                $dir = Split-Path $dest -Parent
                ssh $Chi404Host "mkdir -p '$dir'" 2>$null | Out-Null
                scp -q $_.FullName "${Chi404Host}:${dest}" 2>$null
            }
    }
    Start-Sleep -Seconds $IntervalSec
}
