"""Artifact path registry + safe readers for the cockpit backend.

Single source of truth for *where* every dashboard value comes from. Every
path here points at a file the pipeline already writes; the cockpit never
writes to any of them. Readers are defensive: a missing/!corrupt artifact
yields ``None`` (or an empty default), never an exception — a stage that has
not run yet must render as "not yet run", not crash the dashboard.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# apps/cockpit/backend/paths.py -> repo root is parents[3]
REPO: Path = Path(__file__).resolve().parents[3]


def _p(*parts: str) -> Path:
    return REPO.joinpath(*parts)


# --- Pipeline zone ----------------------------------------------------------
CAPTURE_BASELINE = _p("runtime", "chi404", "baseline", "latest_capture.json")
FEATURE_FABRIC = _p("runtime", "workbench", "feature_fabric_manifest.json")
STAGE_A_RESULT = _p("research_cards", "stage_a_full", "stage_a_result.json")
STAGE_A_SURVIVORS = _p("research_cards", "stage_a_full", "stage_a_survivors.json")
STAGE_B_RESULT = _p("research_cards", "universe_stageb_smoke", "universe_result.json")
M6_RESULT = _p("research_cards", "universe_M6_smoke", "universe_result.json")
ALPHA_CME_SPEC = _p("specs", "ALPHA_CME.md")

# --- System zone ------------------------------------------------------------
LATENCY_SUMMARY = _p("runtime", "latency_reports", "latency_summary.json")
SLOW_TIER_PROBLEMS = _p("runtime", "slow_tier", "problems_latest.json")
SLOW_TIER_EVAL = _p("runtime", "validation", "slow_tier_eval.json")
SLOW_TIER_BRIEF_DIR = _p("artifacts", "research_cards", "slow_tier")
CERT_REGISTRY = _p("runtime", "validation", "certification_registry.json")
DATABENTO_MANIFEST = _p("data", "manifest.parquet")
DATABENTO_RECEIPT = _p("runtime", "databento", "prop_reopen_download_receipt.json")

# --- Models zone ------------------------------------------------------------
RESEARCH_INDEX = _p("research_cards", "all_hypotheses.json")
ALL_HYPOTHESES = _p("artifacts", "research_cards", "all_hypotheses.json")
FILLS_CSV = _p("research_cards", "fills.csv")

# --- Vault (chat RAG, V2) ---------------------------------------------------
def vault_dir() -> Path:
    override = os.environ.get("HFT3_VAULT_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / "Desktop" / "Obsidian Vault From VPS" / "hft3"

# --- Lifecycle zone (ML1/ML4) ----------------------------------------------
MODEL_LIFECYCLE = _p("runtime", "lifecycle", "model_lifecycle.json")
LIFECYCLE_TRANSITIONS = _p("runtime", "lifecycle", "transitions.jsonl")

# --- Control zone (write side, W4) -----------------------------------------
CONTROL_AUDIT_LOG = _p("runtime", "cockpit", "control_audit.jsonl")
ALERT_STATE = _p("runtime", "cockpit", "alert_state.json")

# Directories the file-watcher monitors for live deltas.
WATCH_DIRS = [
    _p("runtime"),
    _p("research_cards"),
]


def read_json(path: Path) -> Optional[Any]:
    """Load JSON, returning None on any miss/parse error."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return None


def read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None


def mtime_iso(path: Path) -> Optional[str]:
    """File modification time as ISO-8601 UTC, or None if absent."""
    try:
        ts = path.stat().st_mtime
    except (FileNotFoundError, OSError):
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def execution_mode() -> str:
    """Current execution mode. REPLAY unless explicitly set live."""
    return os.environ.get("EXECUTION_MODE", "REPLAY").upper()
