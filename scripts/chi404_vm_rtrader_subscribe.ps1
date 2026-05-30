# Post-login: subscribe symbol and nudge Time and Sales export (headless keyboard).
$ErrorActionPreference = "Stop"
$SessionFile = "C:\chi404_rtrader_session.json"
$Symbol = if ($env:RITHMIC_SYMBOL) { $env:RITHMIC_SYMBOL } else { "MES" }
. "$PSScriptRoot\chi404_vm_rtrader_ui.ps1"
Add-Type -AssemblyName System.Windows.Forms

function Write-SessionState {
    param([string]$Note)
    $hwnd = Get-RtraderWindow
    $title = if ($hwnd -ne [IntPtr]::Zero) { [RtraderUi]::GetWindowTitle($hwnd) } else { "" }
    @{
        window_title = $title
        subscribed_symbol = $Symbol
        ts = (Get-Date).ToUniversalTime().ToString("o")
        note = $Note
    } | ConvertTo-Json | Set-Content -Path $SessionFile -Encoding UTF8
}

$deadline = (Get-Date).AddMinutes(10)
$hwnd = [IntPtr]::Zero
while ((Get-Date) -lt $deadline -and $hwnd -eq [IntPtr]::Zero) {
    if (Test-RtraderLoggedIn) {
        $hwnd = Get-RtraderWindow
    }
    if ($hwnd -eq [IntPtr]::Zero) { Start-Sleep -Seconds 5 }
}
if ($hwnd -eq [IntPtr]::Zero) {
    Write-SessionState "subscribe_skipped_not_logged_in"
    Write-Output "Subscribe skipped: R|Trader not logged in"
    exit 0
}

[void][RtraderUi]::FocusWindow($hwnd)
Start-Sleep -Milliseconds 800

[void][System.Windows.Forms.SendKeys]::SendWait("%w")
Start-Sleep -Milliseconds 400
[void][System.Windows.Forms.SendKeys]::SendWait("$Symbol{ENTER}")
Start-Sleep -Seconds 2

[void][System.Windows.Forms.SendKeys]::SendWait("%t")
Start-Sleep -Milliseconds 400

Write-SessionState "subscribe_sent"
Write-Output "Subscribe keystrokes sent for $Symbol"
