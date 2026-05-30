"""Campaign event catalog: model binding + B4 period filter + NPZ presence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

from data_system.src.npz_resolver import resolve_npz_for_event
from data_system.src.events_parser import load_and_parse_events
from decision_engine.python.src.walk_forward import ValidationPeriod, WalkForwardValidator


@dataclass(frozen=True)
class EventSpec:
    event_id: str
    event_type: str
    release_date: str
    event_context: str
    symbol: str
    npz_path: Path
    npz_present: bool
    start_utc: Any
    end_utc: Any
    source: str = ""
    source_url: str = ""
    parsed_symbols: tuple[str, ...] = ()
    npz_symbol_used: str = ""


def _repo_paths(repo_root: Path) -> dict[str, Path]:
    return {
        "binding": repo_root / "workbench" / "config" / "model_event_binding.yaml",
        "walk_forward": repo_root / "workbench" / "config" / "walk_forward.yaml",
        "events_csv": repo_root / "data_system" / "config" / "events.csv",
    }


def row_to_event_context(event_type: str, window_name: str) -> str:
    """Mirror features_engine/src/regime/event_context.py TIGHT/flatten labels."""
    if window_name == "TIGHT":
        if event_type == "CPI":
            return "CPI_TIGHT"
        if event_type == "NFP":
            return "NFP_TIGHT"
        if "FOMC" in str(event_type):
            return "FOMC_STATEMENT_TIGHT"
    if event_type == "PROP_FLATTEN_TOPSTEP":
        return "PROP_FLATTEN_TOPSTEP"
    if event_type == "PROP_REOPEN":
        return "PROP_REOPEN"
    if event_type == "CASH_EQUITY_OPEN" or "OPEN" in str(event_type):
        return "CASH_EQUITY_OPEN"
    if "FRIDAY" in str(event_type):
        return "FRIDAY_CLOSE"
    if "APEX" in str(event_type):
        return "APEX_FLATTEN"
    if "TPT" in str(event_type) or "MyFunded" in str(event_type):
        return "TPT_FLATTEN"
    if "NEWS" in str(event_type):
        return "NEWS_RESTRICTION"
    return str(event_type)


def load_walk_forward_config(repo_root: Path) -> dict[str, Any]:
    path = _repo_paths(repo_root)["walk_forward"]
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_periods(repo_root: Path) -> List[ValidationPeriod]:
    wf = load_walk_forward_config(repo_root)
    if wf.get("periods"):
        return [
            ValidationPeriod(p["name"], int(p["start_year"]), int(p["end_year"]))
            for p in wf["periods"]
        ]
    return WalkForwardValidator().periods


def load_model_binding(repo_root: Path, model_id: str) -> dict[str, Any]:
    path = _repo_paths(repo_root)["binding"]
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if model_id.startswith("PDF_MODEL_"):
        cfg = raw.get("pdf", {}).get(model_id, {})
    else:
        cfg = raw.get("hypothesis", {}).get(model_id, {})
    if cfg.get("campaign_blocked_reason"):
        raise RuntimeError(cfg["campaign_blocked_reason"])
    required = set(cfg.get("required_event_contexts") or [])
    default_macro = set(cfg.get("default_macro_contexts") or [])
    return {
        "required_event_contexts": required,
        "default_macro_contexts": default_macro,
        "allowed_contexts": required if required else default_macro,
        "campaign_mode": cfg.get("campaign_mode", "mbo"),
        "required_datasets": cfg.get("required_datasets", []),
    }


def _release_year(release_date: str) -> int:
    return datetime.strptime(release_date, "%Y-%m-%d").year


from workbench.src.data.personal_lock import is_locked, is_personal_sandbox_date, personal_date_range


def load_sim_shadow_config(repo_root: Path) -> dict[str, Any]:
    wf = load_walk_forward_config(repo_root)
    return wf.get("sim_shadow") or {}


def list_campaign_events(
    model_id: str,
    period: ValidationPeriod,
    symbol: str,
    repo_root: Path,
    *,
    events_csv: Optional[Path] = None,
    mode: str = "promotion",
) -> List[EventSpec]:
    binding = load_model_binding(repo_root, model_id)
    allowed: Set[str] = binding["allowed_contexts"]
    csv_path = events_csv or _repo_paths(repo_root)["events_csv"]
    df = load_and_parse_events(str(csv_path))
    specs: List[EventSpec] = []

    for _, row in df.iterrows():
        release_date = str(row["release_date"])
        if mode == "promotion" and is_personal_sandbox_date(release_date, repo_root):
            continue
        if mode == "personal":
            if not is_personal_sandbox_date(release_date, repo_root):
                continue
            if is_locked(repo_root):
                continue
        year = _release_year(release_date)
        if year < period.start_year or year > period.end_year:
            continue
        syms = row["parsed_symbols"]
        if symbol not in syms:
            continue
        ctx = row_to_event_context(str(row["event_type"]), str(row["window_name"]))
        if ctx not in allowed:
            continue
        eid = str(row["event_id"])
        parsed = tuple(str(s) for s in syms)
        npz, present, sym_used = resolve_npz_for_event(repo_root, eid, symbol, parsed)
        specs.append(
            EventSpec(
                event_id=eid,
                event_type=str(row["event_type"]),
                release_date=str(row["release_date"]),
                event_context=ctx,
                symbol=symbol,
                npz_path=npz,
                npz_present=present,
                start_utc=row["start_utc"],
                end_utc=row["end_utc"],
                source=str(row.get("source", "")),
                source_url=str(row.get("source_url", "")),
                parsed_symbols=parsed,
                npz_symbol_used=sym_used if present else "",
            )
        )
    specs.sort(key=lambda s: s.release_date)
    return specs


def catalog_years_available(
    model_id: str,
    symbol: str,
    repo_root: Path,
) -> int:
    years: set[int] = set()
    for period in load_periods(repo_root):
        for ev in list_campaign_events(model_id, period, symbol, repo_root):
            if ev.npz_present:
                years.add(_release_year(ev.release_date))
    return len(years)


def campaign_preview(
    model_id: str,
    symbol: str,
    repo_root: Path,
) -> Dict[str, Any]:
    periods_out: Dict[str, Any] = {}
    for period in load_periods(repo_root):
        events = list_campaign_events(model_id, period, symbol, repo_root)
        periods_out[period.name] = {
            "start_year": period.start_year,
            "end_year": period.end_year,
            "events": [
                {
                    "event_id": e.event_id,
                    "release_date": e.release_date,
                    "event_context": e.event_context,
                    "npz_present": e.npz_present,
                    "npz_symbol_used": e.npz_symbol_used,
                }
                for e in events
            ],
        }
    binding = load_model_binding(repo_root, model_id)
    return {
        "model_id": model_id,
        "symbol": symbol,
        "allowed_contexts": sorted(binding["allowed_contexts"]),
        "catalog_years": catalog_years_available(model_id, symbol, repo_root),
        "personal_locked": is_locked(repo_root),
        "personal_range": list(personal_date_range(repo_root)),
        "periods": periods_out,
    }


def list_personal_events(
    model_id: str,
    symbol: str,
    repo_root: Path,
) -> List[EventSpec]:
    if is_locked(repo_root):
        return []
    wf = load_walk_forward_config(repo_root)
    start, end = personal_date_range(repo_root)
    period = ValidationPeriod("Personal", int(start[:4]), int(end[:4]))
    return list_campaign_events(model_id, period, symbol, repo_root, mode="personal")


def write_campaign_manifest(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
