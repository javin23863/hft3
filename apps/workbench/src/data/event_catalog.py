"""Campaign event catalog: model binding + B4 period filter + NPZ presence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from zoneinfo import ZoneInfo

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
    row_status: str = ""
    universe_scope: str = "runnable"
    runnable_eligible: bool = True
    model_eligible: bool = True
    symbol_eligible: bool = True
    source_file: str = ""


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
        "event_context_policy": cfg.get(
            "event_context_policy",
            "explicit_filter" if required else "catalog_all_contexts",
        ),
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
                row_status=str(row.get("row_status", "SOURCED")),
                universe_scope="promotion_campaign",
                runnable_eligible=True,
                model_eligible=True,
                symbol_eligible=True,
                source_file=str(csv_path),
            )
        )
    specs.sort(key=lambda s: s.release_date)
    return specs


def _anchor_utc(release_date: str, release_time: str, timezone_name: str) -> datetime:
    local = datetime.fromisoformat(f"{release_date}T{release_time}").replace(tzinfo=ZoneInfo(timezone_name))
    return local.astimezone(ZoneInfo("UTC"))


def _event_id(event_type: str, release_date: str, window_name: str) -> str:
    y, m, d = release_date.split("-")
    return f"{event_type}_{y}_{m}_{d}_{window_name}"


def list_universe_events(
    model_id: str,
    period: ValidationPeriod,
    symbol: str,
    repo_root: Path,
    *,
    include_seed: bool = True,
    statuses: Optional[Set[str]] = None,
    mode: str = "promotion",
    filter_by_model_binding: bool = False,
    filter_by_symbol: bool = True,
) -> List[EventSpec]:
    """List canonical economic-event universe rows, including seed rows when requested.

    This is the data-planning/catalog view. `list_campaign_events()` remains the
    sourced runnable campaign view used by promotion-grade backtests.
    """
    from economic_event_universe.service import list_calendar_rows
    from economic_event_universe.windows import download_window, window_name_for
    from economic_event_universe.registry import get_event_def

    binding = load_model_binding(repo_root, model_id)
    allowed: Set[str] = binding["allowed_contexts"]
    allowed_statuses = {s.upper() for s in statuses} if statuses else None
    specs: List[EventSpec] = []

    for row in list_calendar_rows(repo_root, include_seed=include_seed, statuses=allowed_statuses):
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

        event_type = str(row["event_type"])
        try:
            cfg = get_event_def(event_type)
        except Exception:
            continue
        parsed = tuple(s.strip() for s in str(cfg.get("symbol_universe", "")).split(",") if s.strip())
        symbol_eligible = symbol in parsed
        if filter_by_symbol and not symbol_eligible:
            continue

        window_name = window_name_for(event_type)
        ctx = row_to_event_context(event_type, window_name)
        model_eligible = not allowed or ctx in allowed
        if filter_by_model_binding and not model_eligible:
            continue

        release_time = str(row.get("release_time") or cfg.get("anchor_time", "08:30:00"))
        timezone_name = str(row.get("timezone") or cfg.get("timezone", "America/New_York"))
        start_offset, end_offset = download_window(event_type)
        anchor = _anchor_utc(release_date, release_time, timezone_name)
        start_utc = anchor + timedelta(seconds=int(start_offset))
        end_utc = anchor + timedelta(seconds=int(end_offset))
        eid = _event_id(event_type, release_date, window_name)
        npz, present, sym_used = resolve_npz_for_event(repo_root, eid, symbol, parsed)
        specs.append(
            EventSpec(
                event_id=eid,
                event_type=event_type,
                release_date=release_date,
                event_context=ctx,
                symbol=symbol,
                npz_path=npz,
                npz_present=present,
                start_utc=start_utc,
                end_utc=end_utc,
                source=str(row.get("source", "")),
                source_url=str(row.get("source_url", "")),
                parsed_symbols=parsed,
                npz_symbol_used=sym_used if present else "",
                row_status=str(row.get("row_status", "")),
                universe_scope="full_universe" if include_seed else "sourced_universe",
                runnable_eligible=bool(row.get("runnable_eligible", False)),
                model_eligible=model_eligible,
                symbol_eligible=symbol_eligible,
                source_file=str(row.get("source_file", "")),
            )
        )
    specs.sort(key=lambda s: (s.release_date, s.event_type, s.event_id))
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
        "event_context_policy": binding["event_context_policy"],
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
