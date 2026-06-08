"""Evidence snapshot writer for the active autonomous campaign.

A single JSON file the UI can read in one shot. Written by the
all_lanes orchestrator after each job; the UI reads the latest
copy for the active campaign.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def read_snapshot(artifact_dir: Path) -> Dict[str, Any]:
    artifact_dir = Path(artifact_dir)
    p = artifact_dir / "evidence_snapshot.json"
    if not p.is_file():
        return {"state": "unknown", "artifact_dir": str(artifact_dir)}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "corrupt", "artifact_dir": str(artifact_dir)}


def read_evidence_or_summary(artifact_dir: Path) -> Dict[str, Any]:
    """UI helper: read evidence_snapshot.json first, fall back to summary.json."""
    artifact_dir = Path(artifact_dir)
    snap = read_snapshot(artifact_dir)
    if snap.get("state") not in ("unknown", "corrupt"):
        return snap
    summary = artifact_dir / "summary.json"
    if summary.is_file():
        try:
            return json.loads(summary.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return snap


def list_active(artifact_root: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    artifact_root = Path(artifact_root)
    runs = artifact_root / "workbench_runs"
    if not runs.is_dir():
        runs = artifact_root
    out = []
    if not runs.is_dir():
        return out
    for p in sorted(runs.iterdir(), reverse=True):
        if not p.is_dir():
            continue
        snap = p / "evidence_snapshot.json"
        if snap.is_file():
            try:
                data = json.loads(snap.read_text(encoding="utf-8"))
                data["_artifact_dir"] = str(p)
                out.append(data)
            except (OSError, json.JSONDecodeError):
                out.append({"state": "corrupt", "_artifact_dir": str(p)})
        if len(out) >= limit:
            break
    return out


def is_fresh(artifact_dir: Path, max_age_sec: float = 30.0) -> bool:
    """Return True if the snapshot was updated within max_age_sec.

    Used by the UI to decide whether the active campaign is still
    heartbeating. If the snapshot is older than max_age_sec the runner
    may be hung even if state.json says 'running'.
    """
    snap = Path(artifact_dir) / "evidence_snapshot.json"
    if not snap.is_file():
        return False
    try:
        age = time.time() - snap.stat().st_mtime
        return age <= max_age_sec
    except OSError:
        return False
