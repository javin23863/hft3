"""Weekly Rithmic coverage manifest builder (Phase 1 scaffold)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA_VERSION = "1"


def coverage_manifest_path(repo_root: Path, rithmic_week: str) -> Path:
    safe_week = rithmic_week.replace("-", "_")
    return repo_root / "runtime" / "continuous_cme" / f"coverage_manifest_{safe_week}.json"


def build_coverage_manifest_stub(
    *,
    repo_root: Path,
    rithmic_week: str,
    universe_profile: str,
) -> dict[str, Any]:
    """Return empty manifest shell with required top-level keys (Phase 1)."""
    _ = repo_root  # reserved for future Rithmic root discovery
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "lane": "continuous",
        "rithmic_week": rithmic_week,
        "universe_profile": universe_profile,
        "roots": [],
        "contracts": [],
        "contract_rows": [],
        "summary": {
            "total_contracts": 0,
            "eligible_contracts": 0,
            "total_rows": 0,
            "mean_missing_ratio": None,
        },
    }


def write_coverage_manifest(repo_root: Path, manifest: dict[str, Any]) -> Path:
    week = str(manifest["rithmic_week"])
    path = coverage_manifest_path(repo_root, week)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
