# Post-login: subscribe MES and nudge Time and Sales export (headless keyboard).
$ErrorActionPreference = "Stop"
$SessionFile = "C:\chi404_rtrader_session.json"
$Symbol = if ($env:RITHMIC_SYMBOL) { $env:RITHMIC_SYMBOL } else { "MES" }

Add-Type -AssemblyName System.Windows.Forms

function Write-SessionState {
    param([string]$Note)
    $title = ""
    $proc = Get-Process | Where-Object { $_.MainWindowTitle -match 'Rithmic' } | Select-Object -First 1
    if ($proc) { $title = $proc.MainWindowTitle }
    @{
        window_title = $title
        subscribed_symbol = $Symbol
        ts = (Get-Date).ToUniversalTime().ToString("o")
        note = $Note
    } | ConvertTo-Json | Set-Content -Path $SessionFile -Encoding UTF8
}

$deadline = (Get-Date).AddMinutes(8)
$proc = $null
while ((Get-Date) -lt $deadline -and -not $proc) {
    $proc = Get-Process | Where-Object {
        $_.MainWindowTitle -match 'Rithmic' -and
        $_.MainWindowTitle -notmatch 'Login|waiting for price history'
    } | Select-Object -First 1
    if (-not $proc) { Start-Sleep -Seconds 5 }
}
if (-not $proc) {
    Write-SessionState "subscribe_skipped_not_logged_in"
    Write-Output "Subscribe skipped: R|Trader not logged in"
    exit 0
}

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WinFocus {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    public const int SW_RESTORE = 9;
}
"@
[void][WinFocus]::ShowWindow($proc.MainWindowHandle, [WinFocus]::SW_RESTORE)
[void][WinFocus]::SetForegroundWindow($proc.MainWindowHandle)
Start-Sleep -Milliseconds 800

# Alt menu: attempt Market Watch / symbol entry (R|Trader 17.x on Server 2022).
[void][System.Windows.Forms.SendKeys]::SendWait("%w")
Start-Sleep -Milliseconds 400
[void][System.Windows.Forms.SendKeys]::SendWait("$Symbol{ENTER}")
Start-Sleep -Seconds 2

# Open Time and Sales if bound to Alt+T (common); ignore if menu differs.
[void][System.Windows.Forms.SendKeys]::SendWait("%t")
Start-Sleep -Milliseconds 400

Write-SessionState "subscribe_sent"
Write-Output "Subscribe keystrokes sent for $Symbol"
