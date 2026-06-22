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
ACTIVE_RUN = _p("runtime", "workbench", "active_run.json")
FEATURE_FABRIC = _p("runtime", "workbench", "feature_fabric_manifest.json")
STAGE_A_RESULT = _p("research_cards", "stage_a_full", "stage_a_result.json")
STAGE_A_SURVIVORS = _p("research_cards", "stage_a_full", "stage_a_survivors.json")
STAGE_B_RESULT = _p("research_cards", "universe_stageb_smoke", "universe_result.json")
M6_RESULT = _p("research_cards", "universe_M6_smoke", "universe_result.json")
M6_FULL_RESULT = _p("research_cards", "universe_M6_full", "universe_result.json")
M6_FULL_CHECKPOINT = _p("research_cards", "universe_M6_full", "unit_results.context.json")
VBT_FULL_RUN_DECLARATION = _p("runtime", "reports", "vbt_full_run_declaration.json")
VBT_FULL_UNITS_JSONL = _p("runtime", "reports", "vbt_full_units.jsonl")
VBT_FULL_STATUS = _p("runtime", "reports", "vbt_full_status.json")
VBT_READY_GATE = _p("runtime", "reports", "paid_screen_ready_gate.json")
VBT_PAID_SCREEN_DOC = _p("docs", "project", "VBT_PAID_SCREEN_UNIT_SCOPE.md")
REPO_STATE_DOC = _p("docs", "REPO_STATE.md")
VALIDATION_HONESTY_DOC = _p("docs", "VALIDATION_HONESTY.md")
UNIVERSE_MONITOR_DOC = _p("runtime", "monitor", "universe_M6_full_watch.md")
ALPHA_CME_SPEC = _p("specs", "ALPHA_CME.md")


def pipeline_runs_root() -> Path:
    return _p("research_cards", "pipeline_runs")


def hftbacktest_realism_root() -> Path:
    return _p("research_cards", "hftbacktest_realism")

# --- Options lake -----------------------------------------------------------
def _lake_root() -> Path:
    try:
        from data_system.src.npz_resolver import lake_root
        return lake_root(REPO)
    except ImportError:
        pass
    env = os.environ.get("HFT3_NPZ_ROOT", "").strip()
    if env:
        return Path(env).parent
    return REPO / "data"


OPTIONS_LAKE_ROOT = _lake_root() / "options"
OPTIONS_FIXING_MBO_DIR = OPTIONS_LAKE_ROOT / "fixing_mbo"
OPTIONS_OHLCV_DIR = OPTIONS_LAKE_ROOT / "ohlcv"
OPTIONS_DEFINITIONS_DIR = OPTIONS_LAKE_ROOT / "definitions"
OPTIONS_STATISTICS_DIR = OPTIONS_LAKE_ROOT / "statistics"
LANE_CERT_REPORT = _p("runtime", "validation", "lane_certification_report.json")

# --- System zone ------------------------------------------------------------
LATENCY_SUMMARY = _p("runtime", "latency_reports", "latency_summary.json")
LATENCY_TRUTH = _p("runtime", "latency_reports", "latency_truth.json")
ORDER_ACK_DISTRIBUTION = _p("runtime", "latency_reports", "order_ack_distribution.json")
LATENCY_CURRENT_BASELINE = _p("reports", "latency_baselines", "current_baseline.json")
LATENCY_LIVE_BASELINE = _p("reports", "latency_baselines", "live_r01_chicago_baseline.json")
LATENCY_LIVE_PLACEMENT_CAPABILITY = _p("runtime", "latency_reports", "live_placement_capability.json")
LATENCY_LATEST_ORDER_SUMMARY = _p("reports", "latency_baselines", "order_ack_campaign_20260611T072116Z_summary.json")
LATENCY_DEFENSIVE_CANCEL_SAMPLE = _p("data", "latency_baselines", "2026-06-11", "order_ack_campaign_20260611T071952Z.jsonl")
SLOW_TIER_PROBLEMS = _p("runtime", "slow_tier", "problems_latest.json")
SLOW_TIER_EVAL = _p("runtime", "validation", "slow_tier_eval.json")
SLOW_TIER_BRIEF_DIR = _p("artifacts", "research_cards", "slow_tier")
CERT_REGISTRY = _p("runtime", "validation", "certification_registry.json")
DATABENTO_MANIFEST = (
    Path(os.environ["HFT3_MANIFEST_PATH"])
    if os.environ.get("HFT3_MANIFEST_PATH")
    else _p("data", "manifest.parquet")
)
DATABENTO_RECEIPT = _p("runtime", "databento", "prop_reopen_download_receipt.json")
DATA_DOCTOR_REPORT = _p("runtime", "data_doctor_report.json")

# --- Models zone ------------------------------------------------------------
RESEARCH_INDEX = _p("research_cards", "all_hypotheses.json")
ALL_HYPOTHESES = _p("artifacts", "research_cards", "all_hypotheses.json")
FILLS_CSV = _p("research_cards", "fills.csv")

# --- Portfolio zone (live session reports, Phase 23) ------------------------
# Root the trade_manager session reporter writes to (matches the observer CLI
# default). Each <session_id>/ holds positions.jsonl, pnl_timeseries.jsonl, ...
SESSIONS_ROOT = _p("artifacts", "sessions")

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
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
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
