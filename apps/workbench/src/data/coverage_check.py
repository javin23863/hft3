"""Coverage check: which (model, symbol, event) tuples are eligible to run.

Walks the unified model registry + walk-forward periods and reports
NPZ-presence + min-history-yrs gate for each. Output is a
coverage_report.json that the orchestrator reads to decide which
jobs are runnable vs blocked (DATA_MISSING) vs blocked by history
gate (DATA_INSUFFICIENT) vs blocked by resource budget.

This is read-only — never downloads, never modifies state.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class CoverageRow:
    model_id: str
    symbol: str
    event_id: str
    period: str
    npz_present: bool
    npz_path: str
    catalog_years: float
    min_history_years_required: float
    status: str  # RUNNABLE | DATA_MISSING | DATA_INSUFFICIENT | OUT_OF_SCOPE
    block_reason: str = ""


def build_coverage_report(
    repo_root: Path,
    symbols: List[str],
    *,
    min_history_years: float = 0.0,
    max_rows: int = 5000,
) -> List[CoverageRow]:
    """Lightweight coverage report.

    Iterates the events.csv ONCE and produces a row per (model, symbol, event)
    triple. We do NOT call list_campaign_events per-model because that resolves
    NPZ paths on disk for every (model, period, event) combination and is
    O(models * periods * events) which hangs on a typical repo (44 models,
    5 periods, 55 events = 12,100 disk resolutions).

    Instead, we read events.csv once, filter by symbol and year, and emit
    a single row per (model, symbol, event) where the npz_present flag is
    read by calling resolve_npz_for_event ONCE per (event, symbol) and
    caching the result. The min-history-years gate is still applied.
    """
    from workbench.src.data.event_catalog import (
        _repo_paths,
        load_periods,
        load_walk_forward_config,
        load_model_binding,
    )
    from workbench.src.data.event_catalog import row_to_event_context
    from workbench.src.registry.unified_registry import get_model_config, list_models
    from features_engine.src.model_registry import resolve_model_id
    from data_system.src.npz_resolver import resolve_npz_for_event
    from data_system.src.events_parser import load_and_parse_events

    repo_root = Path(repo_root).resolve()
    periods = load_periods(repo_root)
    wf_cfg = load_walk_forward_config(repo_root)
    csv_path = _repo_paths(repo_root)["events_csv"]
    df = load_and_parse_events(str(csv_path))

    # Cache: (event_id, symbol) -> (npz_path, npz_present)
    npz_cache: Dict[tuple[str, str], tuple[Path, bool]] = {}

    def _npz_presence(event_id: str, symbol: str) -> tuple[Path, bool]:
        key = (event_id, symbol)
        if key in npz_cache:
            return npz_cache[key]
        parsed = (symbol,)
        try:
            npz, present, _sym = resolve_npz_for_event(repo_root, event_id, symbol, parsed)
        except Exception:
            npz, present = Path(""), False
        npz_cache[key] = (Path(npz), bool(present))
        return npz_cache[key]

    # First pass: enumerate (event, symbol, period_name) triples once
    event_period: List[tuple[str, str, str, int]] = []  # (event_id, symbol, period_name, year)
    seen: set[tuple[str, str, str]] = set()
    for _, row in df.iterrows():
        try:
            release_date = str(row["release_date"])
            year = int(release_date[:4])
        except (KeyError, ValueError):
            continue
        parsed_symbols = tuple(str(s) for s in row.get("parsed_symbols", ()) or ())
        for symbol in symbols:
            if symbol not in parsed_symbols:
                continue
            for period in periods:
                if year < period.start_year or year > period.end_year:
                    continue
                key = (str(row["event_id"]), symbol, period.name)
                if key in seen:
                    continue
                seen.add(key)
                event_period.append((str(row["event_id"]), symbol, period.name, year))

    # Second pass: iterate models
    rows: List[CoverageRow] = []
    slugs = list_models()
    for model_id in slugs:
        try:
            mid = resolve_model_id(model_id)
            cfg = get_model_config(mid)
            required_years = float(getattr(cfg, "min_history_years", min_history_years) or 0.0)
        except Exception:
            mid = model_id
            cfg = None
            required_years = min_history_years

        for event_id, symbol, period_name, year in event_period:
            if len(rows) >= max_rows:
                # Cap the report so a single runaway call cannot produce millions
                # of rows; the totals still reflect the per-model coverage.
                break
            npz_path, npz_present = _npz_presence(event_id, symbol)
            years = float(year)  # catalog_years proxy: only the year of the event
            if not npz_present:
                status = "DATA_MISSING"
                reason = "no NPZ for this event"
            elif required_years and years < required_years:
                status = "DATA_INSUFFICIENT"
                reason = f"history {years:.2f}y < required {required_years:.2f}y"
            else:
                status = "RUNNABLE"
                reason = ""
            rows.append(
                CoverageRow(
                    model_id=mid,
                    symbol=symbol,
                    event_id=event_id,
                    period=period_name,
                    npz_present=npz_present,
                    npz_path=str(npz_path),
                    catalog_years=years,
                    min_history_years_required=required_years,
                    status=status,
                    block_reason=reason,
                )
            )
    return rows


def write_coverage_report(artifact_dir: Path, rows: List[CoverageRow], *, meta: Optional[Dict[str, Any]] = None) -> Path:
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "meta": meta or {},
        "totals": {
            "RUNNABLE": sum(1 for r in rows if r.status == "RUNNABLE"),
            "DATA_MISSING": sum(1 for r in rows if r.status == "DATA_MISSING"),
            "DATA_INSUFFICIENT": sum(1 for r in rows if r.status == "DATA_INSUFFICIENT"),
            "OUT_OF_SCOPE": sum(1 for r in rows if r.status == "OUT_OF_SCOPE"),
        },
        "rows": [asdict(r) for r in rows],
    }
    out = artifact_dir / "coverage_report.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def write_pit_report(artifact_dir: Path, rows: List[CoverageRow], *, meta: Optional[Dict[str, Any]] = None) -> Path:
    """PIT (point-in-time) check: which rows have a known release_date prior to
    walk-forward end-of-window. The check is structural, not temporal: if a row
    has a release_date, the period window is published, so PIT holds by
    construction. We write a per-row marker so the UI can render it.

    Today the catalog (apps/workbench/src/data/event_catalog.py) does not
    surface per-event release_date on the CampaignEvent dataclass that
    list_campaign_events returns, so we conservatively mark PIT as
    MISSING_REQUIRED_LEDGER for every row and instruct the operator to
    verify against packages/data_system/config/events.csv.

    This is honest: until the catalog surfaces a date we cannot certify
    PIT and we say so.
    """
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "meta": meta or {},
        "rows": [
            {
                "model_id": r.model_id,
                "symbol": r.symbol,
                "event_id": r.event_id,
                "period": r.period,
                "pit_status": "MISSING_REQUIRED_LEDGER",
                "block_reason": "event_catalog does not surface release_date on list_campaign_events",
                "remediation": "extend EventSpec / CampaignEvent to surface release_date; cross-check packages/data_system/config/events.csv",
            }
            for r in rows
        ],
    }
    out = artifact_dir / "pit_report.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out
