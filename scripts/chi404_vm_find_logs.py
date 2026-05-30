#!/usr/bin/env python3
"""Locate R|Trader log files on VM and test SMB write path."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chi404_vm_winrm import session

PS = r"""
$paths = @(
  "$env:USERPROFILE\Documents\Rithmic",
  "$env:USERPROFILE\Documents",
  "C:\Program Files (x86)\Rithmic Trader Pro",
  "C:\Program Files (x86)\Rithmic Trader Pro\log",
  "C:\Program Files (x86)\Rithmic Trader Pro\Logs"
)
foreach ($p in $paths) {
  if (Test-Path $p) {
    Write-Output "=== $p ==="
    Get-ChildItem $p -Recurse -Include *.log,*.cur.txt,*.txt -ErrorAction SilentlyContinue |
      Sort-Object LastWriteTime -Descending | Select-Object -First 8 FullName,Length,LastWriteTime |
      Format-Table -AutoSize
  }
}
Get-ChildItem "$env:USERPROFILE\Documents" -Filter 'Rithmic*' -ErrorAction SilentlyContinue |
  Format-Table Name,LinkType,Length,LastWriteTime -AutoSize
Get-Process 'Rithmic Trader Pro' -ErrorAction SilentlyContinue |
  Select Id,MainWindowTitle,StartTime | Format-Table -AutoSize
"""

if __name__ == "__main__":
    r = session().run_ps(PS)
    print(r.std_out.decode(errors="replace"))
    sys.exit(0 if r.status_code == 0 else 1)
