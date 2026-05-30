# Headless sidecar: auto-logon, no lock, scheduled R|Trader + login (no VNC required).
param(
    [Parameter(Mandatory = $true)]
    [string]$AdminPassword
)

$ErrorActionPreference = "Stop"
if (-not $AdminPassword) {
    throw "AdminPassword required (set VM_ADMIN_PASSWORD on deploy host)"
}

$exe = "C:\Program Files (x86)\Rithmic Trader Pro\Rithmic Trader Pro.exe"
if (-not (Test-Path $exe)) {
    throw "R|Trader not installed at $exe - run chi404_vm_guest_setup.ps1 first"
}

# Keep console session up through reboots (no lock / sleep).
powercfg /change monitor-timeout-ac 0 | Out-Null
powercfg /change standby-timeout-ac 0 | Out-Null
powercfg /change hibernate-timeout-ac 0 | Out-Null
New-Item -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization" -Force | Out-Null
Set-ItemProperty "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization" -Name NoLockScreen -Value 1
Set-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" -Name DisableLockWorkstation -Value 1

# Auto-logon Administrator (console session for GUI apps).
Set-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" -Name AutoAdminLogon -Value "1"
Set-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" -Name DefaultUserName -Value "Administrator"
Set-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" -Name DefaultPassword -Value $AdminPassword
Set-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" -Name ForceAutoLogon -Value "1"

$principal = New-ScheduledTaskPrincipal -UserId "Administrator" -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

function Register-LogonTask {
    param([string]$Name, [string]$Execute, [string]$Args = $null, [string]$Delay = "PT0S")
    if ($Args) {
        $action = New-ScheduledTaskAction -Execute $Execute -Argument $Args
    } else {
        $action = New-ScheduledTaskAction -Execute $Execute
    }
    $logon = New-ScheduledTaskTrigger -AtLogOn -User "Administrator"
    $logon.Delay = $Delay
    $boot = New-ScheduledTaskTrigger -AtStartup
    $boot.Delay = $Delay
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger @($logon, $boot) -Principal $principal -Settings $settings -Force | Out-Null
}

Register-LogonTask "HFT3-MapSMB" "powershell.exe" "-ExecutionPolicy Bypass -WindowStyle Hidden -File C:\chi404_vm_map_smb.ps1" "PT60S"
Register-LogonTask "HFT3-RithmicTrader" $exe $null "PT120S"
Register-LogonTask "HFT3-RithmicLogin" "powershell.exe" "-ExecutionPolicy Bypass -WindowStyle Hidden -File C:\chi404_vm_rtrader_login.ps1" "PT180S"
Register-LogonTask "HFT3-RithmicSubscribe" "powershell.exe" "-ExecutionPolicy Bypass -WindowStyle Hidden -File C:\chi404_vm_rtrader_subscribe.ps1" "PT240S"

Write-Output "Headless tasks registered: MapSMB (60s), R|Trader (120s), Login (180s), Subscribe (240s)"
