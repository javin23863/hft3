#!/usr/bin/env python3
"""Trigger REAL paper order sweep in VM interactive session (not WinRM session 0)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chi404_vm_winrm import repo_root, session, upload_file

PS1 = repo_root() / "scripts" / "chi404_vm_paper_order_sweep.ps1"
UI_PS1 = repo_root() / "scripts" / "chi404_vm_rtrader_ui.ps1"
LOGIN_PS1 = repo_root() / "scripts" / "chi404_vm_rtrader_login.ps1"
VM_PS1 = "C:\\chi404_vm_paper_order_sweep.ps1"
VM_UI = "C:\\chi404_vm_rtrader_ui.ps1"
VM_LOGIN = "C:\\chi404_vm_rtrader_login.ps1"


def main() -> int:
    target = int(os.environ.get("PAPER_LATENCY_TARGET_ORDERS", "1000"))

    if "Add-Content" in PS1.read_text(encoding="utf-8"):
        print("chi404_vm_paper_order_sweep.ps1 must not use Add-Content", file=sys.stderr)
        return 1

    sess = session()
    for dest, src in (
        (VM_UI, UI_PS1),
        (VM_LOGIN, LOGIN_PS1),
        (VM_PS1, PS1),
    ):
        upload_file(sess, dest, src.read_bytes())
        print(f"uploaded {dest}")

    runner = Path(__file__).resolve().parent / "chi404_vm_run_interactive.py"
    if not runner.is_file():
        print(f"missing {runner}", file=sys.stderr)
        return 1

    env = os.environ.copy()
    env["PAPER_LATENCY_TARGET_ORDERS"] = str(target)
    sym = os.environ.get("RITHMIC_SYMBOL", "MES")
    env["RITHMIC_SYMBOL"] = sym
    r = subprocess.run(
        [
            sys.executable,
            str(runner),
            "--script",
            VM_PS1,
            "--task",
            "HFT3-PaperSweepOnce",
            "--wait-sec",
            "7200",
            "--min-confirmed",
            str(target),
        ],
        env=env,
        check=False,
    )
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
