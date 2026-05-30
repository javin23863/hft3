# Run after R|Trader starts in VM - login from C:\rithmic_login.env staged on CHI404.
$ErrorActionPreference = "Stop"
$envFile = "C:\rithmic_login.env"
if (-not (Test-Path $envFile)) {
    throw "Missing $envFile - run infrastructure/chi404/10_rtrader_smb_share.sh on CHI404"
}
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') { Set-Item -Path "env:$($matches[1])" -Value $matches[2] }
}
$user = $env:RITHMIC_USERNAME
$pass = $env:RITHMIC_PASSWORD
if (-not $user -or -not $pass) { throw "Missing RITHMIC_USERNAME/PASSWORD in $envFile" }

$uiEnv = $env:RITHMIC_UI_ENVIRONMENT
if (-not $uiEnv) {
    if ($env:RITHMIC_ENVIRONMENT -match 'Paper') { $uiEnv = 'Paper' }
    elseif ($env:RITHMIC_ENVIRONMENT) { $uiEnv = $env:RITHMIC_ENVIRONMENT }
    else { $uiEnv = 'Paper' }
}
$uiGw = if ($env:RITHMIC_GATEWAY) { $env:RITHMIC_GATEWAY } else { 'Chicago' }

Add-Type -AssemblyName System.Windows.Forms
$deadline = (Get-Date).AddMinutes(10)
$win = $null
while ((Get-Date) -lt $deadline -and -not $win) {
    $win = Get-Process | Where-Object { $_.MainWindowTitle -match 'Rithmic' } | Select-Object -First 1
    if (-not $win) { Start-Sleep -Seconds 5 }
}
if (-not $win) {
    Write-Error "R|Trader window not found after 10m"
    exit 1
}

[void][System.Windows.Forms.SendKeys]::SendWait("%{TAB}")
Start-Sleep -Milliseconds 500
[void][System.Windows.Forms.SendKeys]::SendWait("$user{TAB}$pass{TAB}{TAB}{TAB}{TAB}{TAB}$uiEnv{TAB}$uiGw{ENTER}")
Write-Output "Login keystrokes sent to R|Trader ($uiEnv / $uiGw)"
