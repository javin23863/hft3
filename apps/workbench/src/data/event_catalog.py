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
    wb = repo_root / "apps" / "workbench"
    if not wb.is_dir():
        wb = repo_root / "workbench"
    ds = repo_root / "packages" / "data_system"
    if not ds.is_dir():
        ds = repo_root / "data_system"
    return {
        "binding": wb / "config" / "model_event_binding.yaml",
        "walk_forward": wb / "config" / "walk_forward.yaml",
        "events_csv": ds / "config" / "events.csv",
    }


def row_to_event_context(event_type: str, window_name: str) -> str:
    """Shared E_t label mapping from economic_event_universe."""
    from economic_event_universe.labels import row_to_event_context as _map

    return _map(event_type, window_name)


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


from features_engine.src.model_registry import legacy_to_slug, load_model_registry, resolve_model_id


def _binding_cfg(raw: dict, slug: str) -> dict:
    entry = load_model_registry().get("models", {}).get(slug, {})
    legacy = entry.get("legacy_id", slug)
    if entry.get("kind") == "pdf_structural":
        return raw.get("pdf", {}).get(slug) or raw.get("pdf", {}).get(legacy) or {}
    return raw.get("hypothesis", {}).get(slug) or raw.get("hypothesis", {}).get(legacy) or {}


def load_model_binding(repo_root: Path, model_id: str) -> dict[str, Any]:
    path = _repo_paths(repo_root)["binding"]
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    slug = resolve_model_id(model_id)
    cfg = _binding_cfg(raw, slug)
    if cfg.get("campaign_blocked_reason"):
        raise RuntimeError(cfg["campaign_blocked_reason"])
    required = set(cfg.get("required_event_contexts") or [])
    return {
        "required_event_contexts": required,
        "allowed_contexts": required,
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
        if allowed and ctx not in allowed:
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
