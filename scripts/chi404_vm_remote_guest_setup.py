#!/usr/bin/env python3
"""Run chi404_vm_guest_setup.ps1 on the Windows VM via WinRM from CHI404."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chi404_vm_winrm import repo_root, session, upload_paths, watch_root


def main() -> int:
    sess = session()
    r = sess.run_cmd("hostname")
    print("hostname:", r.status_code, r.std_out.decode(errors="replace").strip())
    if r.status_code != 0:
        print(r.std_err.decode(errors="replace"), file=sys.stderr)
        return 1

    upload_paths(
        sess,
        {
            "C:/rtrader_smb.env": watch_root() / "rtrader_smb.env",
            "C:/rithmic_login.env": watch_root() / "rithmic_login.env",
            "C:/chi404_vm_map_smb.ps1": repo_root() / "scripts" / "chi404_vm_map_smb.ps1",
            "C:/chi404_vm_guest_setup.ps1": repo_root() / "scripts" / "chi404_vm_guest_setup.ps1",
            "C:/chi404_vm_rtrader_login.ps1": repo_root() / "scripts" / "chi404_vm_rtrader_login.ps1",
        },
        required=True,
    )

    ur = sess.run_ps("powershell -ExecutionPolicy Bypass -File C:\\chi404_vm_guest_setup.ps1")
    print("guest_setup status:", ur.status_code)
    print(ur.std_out.decode(errors="replace"))
    err = ur.std_err.decode(errors="replace")
    if err.strip():
        print("stderr:", err, file=sys.stderr)
    return 0 if ur.status_code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
