# Run after R|Trader starts in VM — Paper/Chicago login from /root/hft3/.env staged on C:\rtrader_smb.env
$ErrorActionPreference = "Stop"
$envFile = "C:\rithmic_login.env"
if (-not (Test-Path $envFile) -and (Test-Path "C:\rtrader_smb.env")) {
    Copy-Item "C:\rtrader_smb.env" $envFile -Force
}
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^([^=]+)=(.*)$') { Set-Item -Path "env:$($matches[1])" -Value $matches[2] }
    }
}
$user = $env:RITHMIC_USERNAME
$pass = $env:RITHMIC_PASSWORD
if (-not $user -or -not $pass) { throw "Missing RITHMIC_USERNAME/PASSWORD" }

Add-Type -AssemblyName System.Windows.Forms
$deadline = (Get-Date).AddMinutes(3)
$win = $null
while ((Get-Date) -lt $deadline -and -not $win) {
    $win = Get-Process | Where-Object { $_.MainWindowTitle -match 'Rithmic' } | Select-Object -First 1
    if (-not $win) { Start-Sleep -Seconds 5 }
}
if (-not $win) { throw "R|Trader window not found" }

[void][System.Windows.Forms.SendKeys]::SendWait("%{TAB}")
Start-Sleep -Milliseconds 500
[void][System.Windows.Forms.SendKeys]::SendWait("$user{TAB}$pass{TAB}{TAB}{TAB}{TAB}{TAB}Paper{TAB}Chicago{ENTER}")
Write-Output "Login keystrokes sent to R|Trader"
