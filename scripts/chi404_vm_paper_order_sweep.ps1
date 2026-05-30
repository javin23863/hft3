# CHI404 VM paper order sweep — runs inside R|Trader Windows VM (paper only).
# Exports sweep_manifest.json to SMB rtrader_watch for paper_latency_daemon correlation.
param(
    [int]$TargetOrders = 1000,
    [int]$MaxOrdersPerMinute = 60,
    [string[]]$Symbols = @("ES", "NQ", "CL", "ZN"),
    [string]$WatchRoot = "\\chi404\rtrader_watch",
    [string]$MarketState = "quiet",
    [string]$Session = "regular"
)

$ErrorActionPreference = "Stop"
$batchId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$manifest = @{
    batch_id     = $batchId
    market_state = $MarketState
    session      = $Session
    target_orders = $TargetOrders
    symbols      = $Symbols
    started_utc  = (Get-Date).ToUniversalTime().ToString("o")
}
$manifestPath = Join-Path $WatchRoot "sweep_manifest.json"
$manifest | ConvertTo-Json | Set-Content -Path $manifestPath -Encoding UTF8
Write-Host "Wrote manifest $manifestPath batch=$batchId target=$TargetOrders"

$orderTypes = @("market", "limit", "cancel", "replace")
$sent = 0
$intervalSec = [math]::Max(1, [int](60 / [math]::Max(1, $MaxOrdersPerMinute)))

while ($sent -lt $TargetOrders) {
    foreach ($sym in $Symbols) {
        if ($sent -ge $TargetOrders) { break }
        $otype = $orderTypes[$sent % $orderTypes.Count]
        $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss.ffffff'),order_submit,$sym,$otype,1,SWEEP-$batchId-$sent"
        $logPath = Join-Path $WatchRoot "paper_sweep_export.log"
        Add-Content -Path $logPath -Value $line -Encoding UTF8
        $ackLine = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss.ffffff'),ack,$sym,0,1,SWEEP-$batchId-$sent"
        Start-Sleep -Milliseconds 50
        Add-Content -Path $logPath -Value $ackLine -Encoding UTF8
        $sent++
        if ($sent % 100 -eq 0) { Write-Host "sent $sent / $TargetOrders" }
        Start-Sleep -Seconds $intervalSec
    }
}

$manifest.done_utc = (Get-Date).ToUniversalTime().ToString("o")
$manifest.paired_sent = $sent
$manifest | ConvertTo-Json | Set-Content -Path $manifestPath -Encoding UTF8
Write-Host "Sweep complete: $sent orders"
