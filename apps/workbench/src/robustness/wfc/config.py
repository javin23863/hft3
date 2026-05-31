"""WFC configuration loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml


def load_wfc_config(repo_root: Path) -> Dict[str, Any]:
    from hft3_bootstrap import workbench_root

    path = workbench_root(repo_root) / "config" / "wfc_gate.yaml"
    if not path.is_file():
        return {"enabled": False}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
