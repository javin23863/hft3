#!/usr/bin/env python3
"""Reboot Windows VM and wait for R|Trader session + SMB log growth."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chi404_vm_winrm import session

VM = "hft3-rtrader-win"
WAIT_SEC = int(__import__("os").environ.get("VM_BOOT_WAIT_SEC", "300"))


def reboot_vm() -> None:
    try:
        subprocess.run(["virsh", "reboot", VM], check=True, timeout=30)
        print(f"virsh reboot {VM} sent")
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"virsh reboot failed ({exc}); trying WinRM shutdown")
        r = session().run_ps("Restart-Computer -Force")
        if r.status_code != 0:
            raise SystemExit("VM reboot failed")


def wait_winrm(deadline: float) -> bool:
    while time.time() < deadline:
        try:
            r = session().run_ps("hostname")
            if r.status_code == 0:
                print("WinRM up:", r.std_out.decode().strip())
                return True
        except Exception:
            pass
        time.sleep(15)
    return False


def main() -> int:
    reboot_vm()
    deadline = time.time() + WAIT_SEC
    print(f"waiting up to {WAIT_SEC}s for VM WinRM...")
    if not wait_winrm(deadline):
        print("FAIL: WinRM did not return", file=sys.stderr)
        return 1

    # Remap SMB after boot
    time.sleep(90)
    from chi404_vm_smb_repair import main as repair

    repair()

    # Poll session state
    for i in range(24):
        r = session().run_ps(
            "Get-Content C:\\chi404_rtrader_session.json -Raw -ErrorAction SilentlyContinue; "
            "Get-Process 'Rithmic Trader Pro' -ErrorAction SilentlyContinue | "
            "Select-Object Id,MainWindowTitle | Format-List"
        )
        out = r.std_out.decode(errors="replace")
        print(f"--- poll {i+1} ---")
        print(out)
        if "logged_in_guess" in out and "true" in out.lower():
            print("Login guess OK")
            return 0
        time.sleep(30)
    print("WARN: login not confirmed; check session manually", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
