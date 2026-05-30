#!/usr/bin/env python3
"""Deploy headless R|Trader autostart to Windows VM via WinRM (CHI404)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chi404_vm_winrm import (
    repo_root,
    require_vm_password,
    run_ps1_with_password,
    session,
    upload_paths,
    watch_root,
)

REQUIRED = {
    "C:/rtrader_smb.env": watch_root() / "rtrader_smb.env",
    "C:/rithmic_login.env": watch_root() / "rithmic_login.env",
    "C:/chi404_vm_map_smb.ps1": repo_root() / "scripts" / "chi404_vm_map_smb.ps1",
    "C:/chi404_vm_headless.ps1": repo_root() / "scripts" / "chi404_vm_headless.ps1",
    "C:/chi404_vm_rtrader_login.ps1": repo_root() / "scripts" / "chi404_vm_rtrader_login.ps1",
    "C:/chi404_vm_rtrader_subscribe.ps1": repo_root() / "scripts" / "chi404_vm_rtrader_subscribe.ps1",
}


def main() -> int:
    pw = require_vm_password()
    sess = session()
    upload_paths(sess, REQUIRED, required=True)
    ur = run_ps1_with_password(sess, "C:\\chi404_vm_headless.ps1", pw)
    print("headless status:", ur.status_code)
    print(ur.std_out.decode(errors="replace"))
    err = ur.std_err.decode(errors="replace")
    if err.strip():
        print("stderr:", err[:600], file=sys.stderr)
    return 0 if ur.status_code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
