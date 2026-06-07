from __future__ import annotations

import os
import socket
import sys


def is_windows() -> bool:
    return sys.platform == "win32"


def is_chi404_runtime() -> bool:
    if is_windows():
        return False
    candidates = [
        os.environ.get("HFT3_DEPLOY_HOST", ""),
        os.environ.get("HFT3_HOST_ID", ""),
        os.environ.get("HOSTNAME", ""),
        os.environ.get("COMPUTERNAME", ""),
    ]
    try:
        candidates.append(socket.gethostname())
    except OSError:
        pass
    return any(value.strip().lower() == "chi404" for value in candidates if value)
