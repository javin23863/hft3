"""Shared JSON output helpers for workbench CLI commands."""
from __future__ import annotations

import json
from typing import Any


def emit_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2))
