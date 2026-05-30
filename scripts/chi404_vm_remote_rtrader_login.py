#!/usr/bin/env python3
"""Retry R|Trader login via WinRM (interactive session may still fail; prefer headless tasks)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chi404_vm_winrm import session


def main() -> int:
    s = session()
    r = s.run_ps(
        "Start-ScheduledTask -TaskName 'HFT3-RithmicTrader' -ErrorAction SilentlyContinue; "
        "Start-Sleep -Seconds 5; "
        "Get-Process | Where-Object { $_.ProcessName -match 'Rithmic' } | Select-Object ProcessName,Id"
    )
    print(r.std_out.decode(errors="replace"))

    for wait in (30, 60, 90):
        time.sleep(wait)
        r = s.run_ps(
            "Get-Process | Where-Object { $_.MainWindowTitle -match 'Rithmic' } | "
            "Select-Object -First 1 -ExpandProperty MainWindowTitle"
        )
        title = r.std_out.decode(errors="replace").strip()
        print(f"wait={wait}s window={title!r}")
        if title:
            break

    r = s.run_ps("powershell -ExecutionPolicy Bypass -File C:\\chi404_vm_rtrader_login.ps1")
    print("login status:", r.status_code)
    print(r.std_out.decode(errors="replace"))
    err = r.std_err.decode(errors="replace")
    if err.strip():
        print("stderr:", err[:800], file=sys.stderr)
    return 0 if r.status_code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
