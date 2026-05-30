#!/usr/bin/env python3
"""Extended WinRM diagnostics for CHI404 Windows VM sidecar."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chi404_vm_winrm import session

CHECKS = [
    "net use",
    "Get-ScheduledTask HFT3-* | Format-Table TaskName,State -AutoSize",
    "Get-Item C:\\Users\\Administrator\\Documents\\Rithmic -ErrorAction SilentlyContinue | Select-Object FullName,LinkType,Target | Format-List",
    "Get-ChildItem C:\\Users\\Administrator\\Documents\\Rithmic -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 10 Name,Length,LastWriteTime | Format-Table -AutoSize",
    "Get-Process 'Rithmic Trader Pro' -ErrorAction SilentlyContinue | Select-Object Id,MainWindowTitle,StartTime | Format-Table -AutoSize",
]


def main() -> int:
    for ps in CHECKS:
        r = session().run_ps(ps)
        print("===", ps.split()[0], "===")
        print(r.std_out.decode(errors="replace").strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
