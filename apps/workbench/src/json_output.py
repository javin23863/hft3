"""Shared JSON output helpers for workbench CLI commands."""
from __future__ import annotations

import json
import sys
from typing import Any


def emit_json(data: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(data, indent=2) + "\n")
