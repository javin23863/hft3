from __future__ import annotations

import os
import sys
from pathlib import Path


def default_windows_watch_dirs() -> list[Path]:
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    roaming = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    candidates = [
        home / "Documents" / "Rithmic",
        home / "Documents" / "RTrader Pro",
        local / "Rithmic",
        local / "RTrader Pro",
        roaming / "Rithmic",
        roaming / "RTrader Pro",
        Path(r"C:\Program Files\Rithmic"),
        Path(r"C:\Program Files (x86)\Rithmic"),
        Path(r"C:\Program Files\Rithmic\RTrader Pro"),
        Path(r"C:\Program Files (x86)\Rithmic\RTrader Pro"),
    ]
    return [p for p in candidates if p.exists()]


def discover_rtrader_exe() -> Path | None:
    env = os.environ.get("RTRADER_EXE_PATH") or os.environ.get("RTRADER_EXE")
    if env and Path(env).exists():
        return Path(env)

    names = ("RTrader.exe", "RTraderPro.exe", "R|Trader Pro.exe")
    search_roots = [
        Path(r"C:\Program Files\Rithmic"),
        Path(r"C:\Program Files (x86)\Rithmic"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Rithmic",
    ]
    for root in search_roots:
        if not root.exists():
            continue
        for name in names:
            for hit in root.rglob(name):
                if hit.is_file():
                    return hit
    return None


def is_windows() -> bool:
    return sys.platform == "win32"
