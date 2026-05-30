# REAL R|Trader paper market orders via UI — never writes synthetic order log lines to SMB watch.
param(
    [int]$TargetOrders = 1000,
    [int]$MaxOrdersPerMinute = 60,
    [string[]]$Symbols = @(),
    [string]$WatchRoot = "\\192.168.122.1\rtrader_watch",
    [string]$MarketState = "quiet",
    [string]$Session = "regular",
    [switch]$MarketOnly
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\chi404_vm_rtrader_ui.ps1"
Add-Type -AssemblyName System.Windows.Forms

if (-not $Symbols -or $Symbols.Count -eq 0) {
    $sym = if ($env:RITHMIC_SYMBOL) { $env:RITHMIC_SYMBOL } else { "MES" }
    $Symbols = @($sym)
}

if (-not (Test-RtraderLoggedIn)) {
    throw "R|Trader not logged in — run chi404_vm_rtrader_login.ps1 in interactive session first"
}

$batchId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$manifest = @{
    batch_id          = $batchId
    market_state      = $MarketState
    session           = $Session
    target_orders     = $TargetOrders
    symbols           = $Symbols
    market_only       = [bool]$MarketOnly
    mode              = "rtrader_ui_real"
    started_utc       = (Get-Date).ToUniversalTime().ToString("o")
    confirmed_export  = 0
    submitted         = 0
}
$manifestPath = Join-Path $WatchRoot "sweep_manifest.json"
$manifest | ConvertTo-Json | Set-Content -Path $manifestPath -Encoding UTF8
Write-Host "REAL UI sweep batch=$batchId target=$TargetOrders symbols=$($Symbols -join ',')"

$intervalSec = [math]::Max(1, [int](60 / [math]::Max(1, $MaxOrdersPerMinute)))
$sent = 0
$confirmed = 0
$symIdx = 0

function Submit-PaperMarketOrder {
    param([string]$Symbol)
    $null = Focus-RtraderWindow
    [void][System.Windows.Forms.SendKeys]::SendWait("{F7}")
    Start-Sleep -Milliseconds 900
    [void][System.Windows.Forms.SendKeys]::SendWait("^a$Symbol{TAB}1{TAB}M{TAB}")
    Start-Sleep -Milliseconds 300
    [void][System.Windows.Forms.SendKeys]::SendWait("%b")
    Start-Sleep -Milliseconds 300
    [void][System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
}

while ($sent -lt $TargetOrders) {
    $sym = $Symbols[$symIdx % $Symbols.Count]
    $symIdx++
    $before = Get-RtraderExportOffsets
    try {
        Submit-PaperMarketOrder -Symbol $sym
    } catch {
        Write-Warning "order UI failed for $sym : $_"
        Start-Sleep -Seconds 5
        continue
    }
    $sent++
    $hit = Wait-RtraderOrderExportLine -BeforeOffsets $before -TimeoutSec 60
    if ($hit.ok) {
        $confirmed++
        Write-Host "confirmed $confirmed/$TargetOrders $sym via $($hit.file): $($hit.line)"
    } else {
        Write-Warning "no R|Trader order export line after submit $sent ($sym)"
    }
    if ($sent % 25 -eq 0) {
        $manifest.submitted = $sent
        $manifest.confirmed_export = $confirmed
        $manifest | ConvertTo-Json | Set-Content -Path $manifestPath -Encoding UTF8
        Write-Host "progress submitted=$sent confirmed=$confirmed"
    }
    Start-Sleep -Seconds $intervalSec
}

$manifest.done_utc = (Get-Date).ToUniversalTime().ToString("o")
$manifest.submitted = $sent
$manifest.confirmed_export = $confirmed
$manifest.mode = "rtrader_ui_real"
$manifest | ConvertTo-Json | Set-Content -Path $manifestPath -Encoding UTF8

if ($confirmed -lt $TargetOrders) {
    throw "Only $confirmed/$TargetOrders orders confirmed via R|Trader-native export lines"
}
Write-Host "REAL UI sweep done submitted=$sent confirmed_export=$confirmed"
