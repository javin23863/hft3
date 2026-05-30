# Run inside Windows VM (FirstLogonCommands or manual).
$ErrorActionPreference = "Stop"

# Load SMB creds from C:\, or copy from a staged CD/floppy (D:..H:).
$envFile = "C:\rtrader_smb.env"
if (-not (Test-Path $envFile)) {
    foreach ($d in @("D","E","F","G","H")) {
        $candidate = "${d}:\rtrader_smb.env"
        if (Test-Path $candidate) {
            Copy-Item $candidate $envFile -Force
            break
        }
    }
}
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^([^=]+)=(.*)$') { Set-Item -Path "env:$($matches[1])" -Value $matches[2] }
    }
}
$SmbHost = $env:RTRADER_SMB_HOST
$SmbUser = $env:RTRADER_SMB_USER
$SmbPass = $env:RTRADER_SMB_PASS
if (-not $SmbHost -or -not $SmbUser -or -not $SmbPass) {
    throw "Missing RTRADER_SMB_* in C:\rtrader_smb.env"
}

# Enable RDP for maintenance (tunnel 3389 over SSH after reboot)
Set-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections -Value 0
Enable-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue

# Install VirtIO storage/network drivers from virtio-win.iso (second CD)
$viIso = Get-WmiObject Win32_CDROMDrive | Where-Object { $_.VolumeName -match 'virtio' } | Select-Object -First 1
if ($viIso) {
    $viRoot = ($viIso.Drive + '\')
    $netInf = Get-ChildItem -Path $viRoot -Recurse -Filter 'netkvm.inf' -ErrorAction SilentlyContinue | Select-Object -First 1
    $storInf = @(
        (Get-ChildItem -Path $viRoot -Recurse -Filter 'viostor.inf' -ErrorAction SilentlyContinue | Select-Object -First 1)
        (Get-ChildItem -Path $viRoot -Recurse -Filter 'vioscsi.inf' -ErrorAction SilentlyContinue | Select-Object -First 1)
    ) | Where-Object { $_ } | Select-Object -First 1
    if ($netInf) { pnputil /add-driver $netInf.FullName /install | Out-Null }
    if ($storInf) { pnputil /add-driver $storInf.FullName /install | Out-Null }
}

$shareRoot = "\\$SmbHost\rtrader_watch"

# SMB credentials + optional R: (zip install uses UNC directly).
cmdkey /add:$SmbHost /user:$SmbUser /pass:$SmbPass | Out-Null
net use $shareRoot /user:$SmbUser $SmbPass /persistent:yes 2>$null | Out-Null

# Install R|Trader from portable zip on share
$zip = "$shareRoot\rithmic_portable.zip"
$dest = "C:\Program Files (x86)\Rithmic Trader Pro"
if ((Test-Path $zip) -and -not (Test-Path "$dest\Rithmic Trader Pro.exe")) {
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Expand-Archive -Path $zip -DestinationPath "C:\Program Files (x86)" -Force
}

# Documents\Rithmic -> UNC watch share (headless log bridge).
if (Test-Path "C:\chi404_vm_map_smb.ps1") {
    powershell -ExecutionPolicy Bypass -File C:\chi404_vm_map_smb.ps1
    if ($LASTEXITCODE -ne 0) { throw "chi404_vm_map_smb.ps1 failed" }
} else {
    $docs = "$env:USERPROFILE\Documents\Rithmic"
    $item = Get-Item $docs -Force -ErrorAction SilentlyContinue
    if ($item -and $item.LinkType -notin @("Junction", "SymbolicLink")) {
        $bak = "$docs.bak_$(Get-Date -Format yyyyMMddHHmmss)"
        Move-Item $docs $bak -Force
    }
    if (-not (Test-Path $docs)) {
        cmd /c mklink /D "$docs" "$shareRoot"
    }
}

$exe = Get-ChildItem -Path "C:\Program Files (x86)" -Recurse -Filter "Rithmic Trader Pro.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $exe) { throw "Rithmic Trader Pro.exe not found" }

# Scheduled task: start R|Trader at logon
$action = New-ScheduledTaskAction -Execute $exe.FullName
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "HFT3-RithmicTrader" -Action $action -Trigger $trigger -Force | Out-Null
Start-ScheduledTask -TaskName "HFT3-RithmicTrader"
Start-Sleep -Seconds 20
if (Test-Path "C:\chi404_vm_rtrader_login.ps1") {
    powershell -ExecutionPolicy Bypass -File C:\chi404_vm_rtrader_login.ps1
}

Write-Output "Guest setup complete: $($exe.FullName) -> logs on $shareRoot (RDP enabled)"
