"""Options zone - first-class read-only CME options lane status.

The options lane is research/backtest only while the lane-scoped defect ledger
is open. This zone reuses the System options readiness primitives so the
cockpit has one source of truth and no new pipeline surface.
"""
from __future__ import annotations

from .. import paths, schemas
from . import system

_BLOCKED_STATUSES = {schemas.FAIL, schemas.MISSING, schemas.STALE, schemas.UNKNOWN}


def _health(data_status: str, defect_status: str, *, research_only: bool) -> str:
    if data_status == schemas.FAIL:
        return schemas.RED
    if data_status in {schemas.MISSING, schemas.STALE, schemas.UNKNOWN} or defect_status == schemas.FAIL or research_only:
        return schemas.AMBER
    return schemas.GREEN


def build() -> dict:
    data = system._options_data_readiness()
    defects = system._options_defect_ledger()
    data_status = str(data.get("status", schemas.UNKNOWN))
    defect_status = str(defects.get("status", schemas.UNKNOWN))
    research_only = True
    blocked_reasons = ["research_only_phase"]
    if data_status in _BLOCKED_STATUSES:
        blocked_reasons.append(f"data_readiness:{data_status}")
    if defect_status == schemas.FAIL:
        blocked_reasons.append("defect_ledger_open")
    return {
        "zone": "options",
        "generated_utc": paths.now_iso(),
        "health": _health(data_status, defect_status, research_only=research_only),
        "lane": "cme_options",
        "model_id_prefix": "FOPT_",
        "phase": "research_backtest_only",
        "research_only": research_only,
        "data_readiness": data,
        "defect_ledger": defects,
        "context_feature_coverage": {
            "status": "not_measured",
            "options_as_clue": "not_measured",
            "options_standalone_strategy": "not_measured",
            "note": "No artifact-level options context-feature coverage is present yet.",
        },
        "shadow_live_status": "blocked",
        "shadow_live_blockers": blocked_reasons,
        "controls": {
            "live_order_controls": False,
            "paper_order_controls": False,
            "reason": "OPTIONS_LANE.md Phases 0-1 are research/backtest only.",
        },
        "authority_sources": [
            "specs/OPTIONS_LANE.md",
            "vault:decisions/2026-06-12 Options-lane build decisions (slices 1-7).md",
            "vault:sessions/2026-06-13 Options backfill, study verdicts, cockpit integration.md",
        ],
    }
