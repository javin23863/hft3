#!/usr/bin/env python3
"""Point R|Trader logs at SMB watch: merge .bak, restart app, verify host files."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chi404_vm_winrm import session

PS = r"""
$ErrorActionPreference = 'Stop'
$envFile = 'C:\rtrader_smb.env'
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') { Set-Item -Path "env:$($matches[1])" -Value $matches[2] }
}
$smbHost = $env:RTRADER_SMB_HOST
$user = $env:RTRADER_SMB_USER
$pass = $env:RTRADER_SMB_PASS
cmdkey /add:$smbHost /user:$user /pass:$pass | Out-Null
net use \\$smbHost\rtrader_watch /user:$user $pass /persistent:yes 2>$null | Out-Null

$docs = "$env:USERPROFILE\Documents\Rithmic"
Get-ScheduledTask HFT3-* -ErrorAction SilentlyContinue | Stop-ScheduledTask -ErrorAction SilentlyContinue
Get-Process 'Rithmic Trader Pro' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3

# Merge any .bak Rithmic folders into the UNC symlink target.
Get-ChildItem "$env:USERPROFILE\Documents" -Directory -Filter 'Rithmic.bak_*' -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending | ForEach-Object {
    Get-ChildItem $_.FullName -File -ErrorAction SilentlyContinue | ForEach-Object {
      Copy-Item $_.FullName (Join-Path $docs $_.Name) -Force
      Write-Output "copied $($_.Name) from $($_.DirectoryName)"
    }
  }

if (Test-Path $docs) {
  Get-ChildItem $docs -File | Select-Object Name,Length,LastWriteTime | Format-Table -AutoSize
}

Start-ScheduledTask -TaskName 'HFT3-RithmicTrader'
Start-Sleep -Seconds 5
Start-ScheduledTask -TaskName 'HFT3-RithmicLogin'
Get-Process 'Rithmic Trader Pro' -ErrorAction SilentlyContinue | Select Id,MainWindowTitle | Format-Table -AutoSize
Write-Output DONE
"""

if __name__ == "__main__":
    r = session().run_ps(PS)
    print(r.std_out.decode(errors="replace"))
    err = r.std_err.decode(errors="replace")
    if err.strip():
        print("stderr:", err[:500], file=sys.stderr)
    sys.exit(0 if r.status_code == 0 else 1)
