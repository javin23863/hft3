#!/usr/bin/env python3
"""Repair VM SMB mapping and verify watch share visibility."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chi404_vm_winrm import session

PS = r"""
$ErrorActionPreference = 'Continue'
Write-Output '=== TCP 445 ==='
Test-NetConnection 192.168.122.1 -Port 445 -WarningAction SilentlyContinue |
  Select-Object ComputerName,RemotePort,TcpTestSucceeded | Format-List
Write-Output '=== map_smb ==='
powershell.exe -ExecutionPolicy Bypass -File C:\chi404_vm_map_smb.ps1
Write-Output "map_smb exit=$LASTEXITCODE"
Write-Output '=== net use ==='
net use
Write-Output '=== list share via UNC ==='
Get-ChildItem \\192.168.122.1\rtrader_watch -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 8 Name,Length,LastWriteTime |
  Format-Table -AutoSize
Write-Output '=== list via symlink ==='
Get-ChildItem C:\Users\Administrator\Documents\Rithmic -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 8 Name,Length,LastWriteTime |
  Format-Table -AutoSize
"""


def main() -> int:
    r = session().run_ps(PS)
    print(r.std_out.decode(errors="replace"))
    err = r.std_err.decode(errors="replace")
    if err.strip():
        print("stderr:", err[:1500], file=sys.stderr)
    return 0 if r.status_code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
