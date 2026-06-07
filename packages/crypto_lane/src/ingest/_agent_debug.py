"""Session debug logging (agent instrumentation)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

_SESSION = "7965fe"
_LOG_NAMES = (
    Path(r"c:\Users\MSI\Documents\GitHub\hft2\debug-7965fe.log"),
)


def agent_debug_log(
    *,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any] | None = None,
    run_id: str = "pre-fix",
) -> None:
    # #region agent log
    payload = {
        "sessionId": _SESSION,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
        "runId": run_id,
    }
    line = json.dumps(payload, default=str) + "\n"
    for path in _LOG_NAMES:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            return
        except OSError:
            continue
    # #endregion
