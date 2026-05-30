#!/usr/bin/env python3
"""Upload fixed UI helper and run R|Trader login on VM."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chi404_vm_winrm import session, upload_file

def main() -> int:
    ui = Path(__file__).resolve().parent / "chi404_vm_rtrader_ui.ps1"
    s = session()
    upload_file(s, "C:\\chi404_vm_rtrader_ui.ps1", ui.read_bytes())
    r = s.run_ps("powershell.exe -ExecutionPolicy Bypass -File C:\\chi404_vm_rtrader_login.ps1")
    print(r.std_out.decode(errors="replace"))
    err = r.std_err.decode(errors="replace")
    if err.strip():
        print("stderr:", err[:2000], file=sys.stderr)
    return 0 if r.status_code == 0 else 1

if __name__ == "__main__":
    raise SystemExit(main())
