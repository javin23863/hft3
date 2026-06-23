#!/usr/bin/env python3
"""Generate JSONL work units for VectorBT paid-compute screening."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from features_engine.src.hypotheses.registry import get_active_hypotheses
from features_engine.src.model_registry import (
    get_hyp_id_for_slug,
    get_slug_for_hyp_id,
    load_model_registry,
    resolve_model_id,
)
from backtest_pipeline.src.research_clock import (
    RESEARCH_CLOCK_SCHEDULED_EVENT,
    validate_research_clock,
)
from backtest_pipeline.src.paid_screen_types import (
    PaidScreenUnit,
    TARGET_ONLY_CONTEXT_SET_ID,
    validate_context_set_id,
)

_DEFAULT_SYMBOL = "MES.v.0"
# CME M6 full symbol universe (CME_M6_SWEEP_CONTROL_PLAN.md)
CME_M6_SYMBOLS = "MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0"
_DEFAULT_THESIS_TEMPLATE = (
    "{display_name} event-window strategy ({model_id}) on {event_type} release for {symbol} event {event_id}"
)
# BLUEPRINT ┬º8 / walk_forward.yaml ΓÇö holdout excluded from discovery prefilter by default
RESEARCH_SPLIT_CHOICES: Dict[str, Optional[List[str]]] = {
    "discovery": ["Discovery"],
    "confirmation": ["Confirmation"],
    "discovery_confirmation": ["Discovery", "Confirmation"],
    "holdout": ["Holdout"],
    "recent_holdout": ["Recent holdout"],
    "all": None,
}
DEFAULT_ALL_ACTIVE_RESEARCH_SPLIT = "discovery_confirmation"


def _display_name_for_slug(slug: str) -> str:
    entry = load_model_registry().get("models", {}).get(slug) or {}
    return str(entry.get("display_name") or slug)


def _hypothesis_model_id(hyp_id: int) -> str:
    return get_slug_for_hyp_id(hyp_id)


def _format_thesis(
    *,
    model_id: str,
    event_type: str,
    symbol: str,
    event_id: str,
    thesis_template: str,
) -> str:
    return thesis_template.format(
        model_id=model_id,
        display_name=_display_name_for_slug(model_id),
        event_type=event_type,
        symbol=symbol,
        event_id=event_id,
    )


def _parse_stage_a_allowed_cells(
    payload: Dict[str, Any],
) -> Set[tuple[int, str]]:
    """Mirror run_event_universe stage-A allowed (hyp_id, event_type) cells."""
    survivors = payload.get("survivors") or []
    pass_through = payload.get("pass_through") or []
    tested_cells = payload.get("tested_cells") or []
    tested_etypes: Set[str] = {
        str(tc["event_type"]).strip()
        for tc in tested_cells
        if isinstance(tc, dict) and tc.get("event_type")
    }
    allowed: Set[tuple[int, str]] = set()

    for row in survivors:
        if not isinstance(row, dict):
            continue
        if "hyp_id" in row and "event_type" in row:
            allowed.add((int(row["hyp_id"]), str(row["event_type"]).strip()))

    for pt in pass_through:
        pt_id: Optional[int] = None
        if isinstance(pt, int):
            pt_id = pt
        elif isinstance(pt, str) and pt.strip().isdigit():
            pt_id = int(pt.strip())
        elif isinstance(pt, dict) and pt.get("hyp_id") is not None:
            pt_id = int(pt["hyp_id"])
        if pt_id is None:
            continue
        for etype in tested_etypes:
            allowed.add((pt_id, etype))

    return allowed


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "unit"


def _normalize_context_set_id(value: str) -> str:
    return validate_context_set_id(value)


def _unit_id_for_context(
    *,
    model_id: str,
    symbol: str,
    event_id: str,
    context_set_id: str,
    ablation_group_id: Optional[str],
) -> str:
    parts = [model_id, symbol, event_id]
    if context_set_id != TARGET_ONLY_CONTEXT_SET_ID:
        parts.append(context_set_id)
    if ablation_group_id:
        parts.append(ablation_group_id)
    return _slug("|".join(parts))


def _parse_declared_context_sets(raw: Optional[str], context_set_id: str) -> List[str]:
    return list(PaidScreenUnit._parse_declared_context_sets(raw, context_set_id))


def _default_negative_control_policy(research_clock: str, context_set_id: str) -> Dict[str, str]:
    if research_clock == RESEARCH_CLOCK_SCHEDULED_EVENT and context_set_id == TARGET_ONLY_CONTEXT_SET_ID:
        return {"status": "not_required", "reason": "target_only_baseline"}
    return {
        "status": "required_before_context_claim",
        "reason": "non_target_context_or_non_scheduled_clock",
    }


def _parse_negative_control_policy(
    raw: Optional[str],
    *,
    research_clock: str,
    context_set_id: str,
) -> Dict[str, Any]:
    if not raw:
        return _default_negative_control_policy(research_clock, context_set_id)
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("negative_control_policy_json must decode to an object")
    return payload


def _stamp_context_metadata(
    unit: Dict[str, Any],
    *,
    research_clock: str,
    context_set_id: str,
    declared_context_sets: List[str],
    ablation_group_id: Optional[str],
    negative_control_policy: Dict[str, Any],
) -> Dict[str, Any]:
    unit["research_clock"] = research_clock
    unit["context_set_id"] = context_set_id
    unit["allowed_context_set_id"] = context_set_id
    unit["declared_context_sets"] = declared_context_sets
    unit["negative_control_policy"] = negative_control_policy
    if ablation_group_id:
        unit["ablation_group_id"] = ablation_group_id
    return unit


def _walk_forward_config_path() -> Path:
    for candidate in (
        _REPO / "apps" / "workbench" / "config" / "walk_forward.yaml",
        _REPO / "workbench" / "config" / "walk_forward.yaml",
    ):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("walk_forward.yaml not found under apps/workbench or workbench/config")


def _load_research_periods() -> List[Dict[str, Any]]:
    cfg = yaml.safe_load(_walk_forward_config_path().read_text(encoding="utf-8")) or {}
    return list(cfg.get("periods") or [])


def _resolve_split_date_bounds(
    research_split: Optional[str],
    *,
    start_date: Optional[str],
    end_date: Optional[str],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Map research_split or explicit dates to inclusive release_date bounds."""
    if start_date or end_date:
        label = research_split or "custom_dates"
        return start_date, end_date, label

    if research_split is None:
        return None, None, None

    if research_split == "all":
        return None, None, "all"

    period_names = RESEARCH_SPLIT_CHOICES.get(research_split)
    if period_names is None:
        raise ValueError(f"unknown research_split: {research_split!r}")

    name_to_period = {p["name"]: p for p in _load_research_periods()}
    selected = [name_to_period[n] for n in period_names if n in name_to_period]
    if not selected:
        raise ValueError(f"no walk-forward periods matched research_split={research_split!r}")

    start = f"{min(int(p['start_year']) for p in selected)}-01-01"
    end = f"{max(int(p['end_year']) for p in selected)}-12-31"
    return start, end, research_split


def _release_date_in_scope(release_date: str, start_date: Optional[str], end_date: Optional[str]) -> bool:
    if not release_date:
        return False
    if start_date and release_date < start_date:
        return False
    if end_date and release_date > end_date:
        return False
    return True


def _load_events(
    events_csv: Path,
    *,
    event_types: Optional[Set[str]],
    symbols: List[str],
    window_name: Optional[str],
    max_rows: Optional[int],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with events_csv.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            event_id = (row.get("event_id") or "").strip()
            event_type = (row.get("event_type") or "").strip()
            release_date = (row.get("release_date") or "").strip()
            if not event_id or not event_type:
                continue
            if event_types and event_type not in event_types:
                continue
            if window_name and (row.get("window_name") or "").strip() != window_name:
                continue
            if not _release_date_in_scope(release_date, start_date, end_date):
                continue
            row_symbols = [s.strip() for s in (row.get("symbols") or "").split(",") if s.strip()]
            matching_symbols = [s for s in symbols if s in row_symbols]
            if not matching_symbols:
                continue
            for symbol in matching_symbols:
                rows.append(
                    {
                        "event_id": event_id,
                        "event_type": event_type,
                        "symbol": symbol,
                        "release_date": release_date,
                    }
                )
                if max_rows is not None and len(rows) >= max_rows:
                    return rows
    return rows


def _units_from_events(
    events: List[Dict[str, Any]],
    *,
    model_id: str,
    thesis_template: str,
    research_split: Optional[str] = None,
    research_clock: str = RESEARCH_CLOCK_SCHEDULED_EVENT,
    context_set_id: str = TARGET_ONLY_CONTEXT_SET_ID,
    declared_context_sets: Optional[List[str]] = None,
    ablation_group_id: Optional[str] = None,
    negative_control_policy: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    units: List[Dict[str, Any]] = []
    declared = declared_context_sets or _parse_declared_context_sets(None, context_set_id)
    nc_policy = negative_control_policy or _default_negative_control_policy(research_clock, context_set_id)
    for ev in events:
        event_id = ev["event_id"]
        symbol = ev["symbol"]
        event_type = ev["event_type"]
        unit_id = _unit_id_for_context(
            model_id=model_id,
            symbol=symbol,
            event_id=event_id,
            context_set_id=context_set_id,
            ablation_group_id=ablation_group_id,
        )
        thesis = _format_thesis(
            model_id=model_id,
            event_type=event_type,
            symbol=symbol,
            event_id=event_id,
            thesis_template=thesis_template,
        )
        unit: Dict[str, Any] = {
            "unit_id": unit_id,
            "event_id": event_id,
            "symbol": symbol,
            "event_type": event_type,
            "model_id": model_id,
            "thesis": thesis,
        }
        if research_split:
            unit["research_split"] = research_split
        _stamp_context_metadata(
            unit,
            research_clock=research_clock,
            context_set_id=context_set_id,
            declared_context_sets=declared,
            ablation_group_id=ablation_group_id,
            negative_control_policy=nc_policy,
        )
        units.append(unit)
    return units


def _active_model_ids() -> List[str]:
    """Canonical slugs for every hypothesis in get_active_hypotheses()."""
    return sorted({get_slug_for_hyp_id(h.hyp_id) for h in get_active_hypotheses()})


def _filter_events_to_runnable_npz(
    events: List[Dict[str, Any]],
    repo_root: Path,
) -> List[Dict[str, Any]]:
    """Drop event-symbol rows with no runnable NPZ before model expansion."""
    keys, manifest_authority_seen = _runnable_npz_key_state(repo_root)
    if keys or manifest_authority_seen:
        return [
            row
            for row in events
            if (
                str(row.get("symbol") or "").strip(),
                str(row.get("event_id") or "").strip(),
            )
            in keys
        ]

    from backtest_pipeline.src.vectorbt_adapter import _npz_candidates_for_event
    from data_system.src.event_data_resolver import npz_search_dirs

    search_dirs = npz_search_dirs(repo_root)
    kept: List[Dict[str, Any]] = []
    for row in events:
        eid = str(row.get("event_id") or "").strip()
        if eid and _npz_candidates_for_event(search_dirs, eid, row.get("symbol")):
            kept.append(row)
    return kept


def _units_from_all_active_models(
    events_csv: Path,
    *,
    symbols: List[str],
    event_types: Optional[Set[str]],
    thesis_template: str,
    window_name: str,
    max_units: Optional[int],
    model_ids: Optional[List[str]],
    start_date: Optional[str],
    end_date: Optional[str],
    research_split: Optional[str],
    require_runnable_npz: bool = False,
    repo_root: Path = _REPO,
    research_clock: str = RESEARCH_CLOCK_SCHEDULED_EVENT,
    context_set_id: str = TARGET_ONLY_CONTEXT_SET_ID,
    declared_context_sets: Optional[List[str]] = None,
    ablation_group_id: Optional[str] = None,
    negative_control_policy: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    ids = model_ids if model_ids is not None else _active_model_ids()
    if not ids:
        raise ValueError("no active model ids resolved")

    split_start, split_end, split_label = _resolve_split_date_bounds(
        research_split,
        start_date=start_date,
        end_date=end_date,
    )

    events = _load_events(
        events_csv,
        event_types=event_types,
        symbols=symbols,
        window_name=window_name,
        max_rows=None,
        start_date=split_start,
        end_date=split_end,
    )
    if require_runnable_npz:
        before = len(events)
        events = _filter_events_to_runnable_npz(events, repo_root)
        print(f"NPZ pre-filter: {before} event-rows -> {len(events)} runnable rows")
    units: List[Dict[str, Any]] = []
    for raw_model_id in ids:
        resolved = resolve_model_id(raw_model_id)
        for unit in _units_from_events(
            events,
            model_id=resolved,
            thesis_template=thesis_template,
            research_split=split_label,
            research_clock=research_clock,
            context_set_id=context_set_id,
            declared_context_sets=declared_context_sets,
            ablation_group_id=ablation_group_id,
            negative_control_policy=negative_control_policy,
        ):
            enriched = dict(unit)
            try:
                enriched["hyp_id"] = get_hyp_id_for_slug(resolved)
            except KeyError:
                pass
            units.append(enriched)
            if max_units is not None and len(units) >= max_units:
                return units
    return units


def _units_from_stage_a_survivors(
    survivors_path: Path,
    events_csv: Path,
    *,
    symbols: List[str],
    event_types: Optional[Set[str]],
    thesis_template: str,
    max_units: Optional[int],
    window_name: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    research_split: Optional[str] = None,
    research_clock: str = RESEARCH_CLOCK_SCHEDULED_EVENT,
    context_set_id: str = TARGET_ONLY_CONTEXT_SET_ID,
    declared_context_sets: Optional[List[str]] = None,
    ablation_group_id: Optional[str] = None,
    negative_control_policy: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    payload = json.loads(survivors_path.read_text(encoding="utf-8"))
    allowed_cells = _parse_stage_a_allowed_cells(payload)
    if not allowed_cells:
        raise ValueError("stage_a_survivors.json: no allowed (hyp_id, event_type) cells")

    allowed_etypes = {etype for _, etype in allowed_cells}
    if event_types:
        allowed_etypes &= event_types
        allowed_cells = {(hyp_id, etype) for hyp_id, etype in allowed_cells if etype in allowed_etypes}
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for row in _load_events(
        events_csv,
        event_types=allowed_etypes,
        symbols=symbols,
        window_name=window_name,
        max_rows=None,
        start_date=start_date,
        end_date=end_date,
    ):
        by_type.setdefault(row["event_type"], []).append(row)

    units: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    declared = declared_context_sets or _parse_declared_context_sets(None, context_set_id)
    nc_policy = negative_control_policy or _default_negative_control_policy(research_clock, context_set_id)
    for hyp_id, event_type in sorted(allowed_cells):
        model_id = _hypothesis_model_id(hyp_id)
        for ev in by_type.get(event_type, []):
            event_id = ev["event_id"]
            symbol = ev["symbol"]
            unit_id = _unit_id_for_context(
                model_id=model_id,
                symbol=symbol,
                event_id=event_id,
                context_set_id=context_set_id,
                ablation_group_id=ablation_group_id,
            )
            if unit_id in seen:
                continue
            seen.add(unit_id)
            record = {
                "unit_id": unit_id,
                "event_id": event_id,
                "symbol": symbol,
                "event_type": event_type,
                "model_id": model_id,
                "hyp_id": hyp_id,
                "thesis": _format_thesis(
                    model_id=model_id,
                    event_type=event_type,
                    symbol=symbol,
                    event_id=event_id,
                    thesis_template=thesis_template,
                ),
            }
            if research_split:
                record["research_split"] = research_split
            _stamp_context_metadata(
                record,
                research_clock=research_clock,
                context_set_id=context_set_id,
                declared_context_sets=declared,
                ablation_group_id=ablation_group_id,
                negative_control_policy=nc_policy,
            )
            units.append(record)
            if max_units is not None and len(units) >= max_units:
                return units
    return units


def _parse_npz_name(path: Path) -> Optional[tuple[str, str]]:
    suffix = "_mbo.npz"
    if not path.name.endswith(suffix):
        return None
    stem = path.name[: -len(suffix)]
    if "_" not in stem:
        return None
    return stem.split("_", 1)


def _first_symbol(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return str(value[0]).strip() if value else ""
    text = str(value or "").strip()
    if not text:
        return ""
    return text.replace("[", "").replace("]", "").replace("'", "").split(",")[0].strip()


def _manifest_path_matches_lake(path: Path, root: Path) -> bool:
    try:
        parent = path.resolve().parent
        return parent == root.resolve() or parent == root.resolve().parent
    except OSError:
        return False


def _manifest_paths(root: Path) -> List[Path]:
    paths: List[Path] = []
    env_path = os.environ.get("HFT3_MANIFEST_PATH", "").strip()
    if env_path:
        path = Path(env_path)
        if _manifest_path_matches_lake(path, root):
            paths.append(path)
            if path.suffix == ".parquet":
                return paths
    catalog = root / "manifest.json"
    if not any(p.resolve() == catalog.resolve() for p in paths if p.exists()):
        paths.append(catalog)
    return paths


def _read_manifest_parquet_records(path: Path) -> List[Dict[str, Any]]:
    try:
        import pandas as pd  # type: ignore[import-not-found]
    except ImportError:
        return []
    if not path.is_file():
        return []
    df = pd.read_parquet(path)
    return [dict(row) for row in df.to_dict("records")]


def _read_manifest_json_records(path: Path) -> tuple[List[Dict[str, Any]], bool]:
    if not path.is_file():
        return [], False
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [rec for rec in payload if isinstance(rec, dict)], True
    if isinstance(payload, dict) and isinstance(payload.get("files"), list):
        return [{"npz_path": name} for name in payload["files"]], True
    return [], False


def _resolve_npz_path(root: Path, repo_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    if len(path.parts) == 1:
        return root / path
    repo_path = repo_root / path
    if repo_path.is_file():
        return repo_path
    return root / path


def _symbol_variants(symbol: str) -> List[str]:
    sym = symbol.strip()
    if not sym:
        return []
    variants = [sym]
    if sym.endswith(".v.0"):
        variants.append(sym[:-4])
    elif "." not in sym:
        variants.append(f"{sym}.v.0")
    return list(dict.fromkeys(variants))


def _candidate_npz_paths(root: Path, repo_root: Path, raw_path: str, symbol: str, event_id: str) -> List[Path]:
    paths: List[Path] = []
    if raw_path:
        resolved = _resolve_npz_path(root, repo_root, raw_path)
        if _parse_npz_name(resolved) is not None:
            paths.append(resolved)
    if symbol and event_id:
        for sym in _symbol_variants(symbol):
            paths.append(root / f"{sym}_{event_id}_mbo.npz")
    deduped: List[Path] = []
    seen: Set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _record_event_count_ok(rec: Dict[str, Any]) -> bool:
    if "event_count" not in rec:
        return True
    try:
        return int(rec["event_count"]) > 0
    except (TypeError, ValueError):
        return False


def _runnable_npz_key_state(repo_root: Path) -> tuple[Set[tuple[str, str]], bool]:
    """Build (symbol, event_id) keys from the lake runnable-NPZ authority."""
    from data_system.src.npz_resolver import npz_root

    root = npz_root(repo_root)
    keys: Set[tuple[str, str]] = set()
    manifest_authority_seen = False

    def add_key(symbol: str, event_id: str) -> None:
        sym = symbol.strip()
        eid = event_id.strip()
        if not sym or not eid:
            return
        keys.add((sym, eid))
        if sym.endswith(".v.0"):
            keys.add((sym[:-4], eid))
        elif "." not in sym:
            keys.add((f"{sym}.v.0", eid))

    for manifest in _manifest_paths(root):
        if manifest.suffix == ".parquet":
            records = _read_manifest_parquet_records(manifest)
            manifest_authority_seen = True
        else:
            records, recognized = _read_manifest_json_records(manifest)
            manifest_authority_seen = recognized or manifest_authority_seen
        for rec in records:
            if rec.get("error") or not _record_event_count_ok(rec):
                continue
            raw_path = str(rec.get("npz_path") or rec.get("path") or "")
            if not raw_path:
                output_path = str(rec.get("output_path") or "")
                if output_path.endswith(".npz"):
                    raw_path = output_path
            sym = str(rec.get("symbol") or rec.get("resolved_symbol") or "").strip()
            if not sym:
                sym = _first_symbol(rec.get("requested_symbol") or rec.get("symbols"))
            eid = str(rec.get("event_id") or "").strip()
            for npz_path in _candidate_npz_paths(root, repo_root, raw_path, sym, eid):
                parsed = _parse_npz_name(npz_path)
                if parsed is None or not npz_path.is_file():
                    continue
                parsed_sym, parsed_eid = parsed
                add_key(sym or parsed_sym, eid or parsed_eid)
    if keys or manifest_authority_seen:
        return keys, manifest_authority_seen

    for npz_path in root.glob("*_mbo.npz"):
        parsed = _parse_npz_name(npz_path)
        if parsed is None:
            continue
        sym, eid = parsed
        add_key(sym, eid)
    return keys, manifest_authority_seen


def _runnable_npz_keys(repo_root: Path) -> Set[tuple[str, str]]:
    keys, _manifest_authority_seen = _runnable_npz_key_state(repo_root)
    return keys


def _filter_runnable_npz_units(
    units: List[Dict[str, Any]],
    repo_root: Path,
) -> List[Dict[str, Any]]:
    """Keep only units whose event_id+symbol resolve to an NPZ under HFT3_NPZ_ROOT."""
    keys, manifest_authority_seen = _runnable_npz_key_state(repo_root)
    if keys or manifest_authority_seen:
        return [
            unit
            for unit in units
            if (
                str(unit.get("symbol") or "").strip(),
                str(unit.get("event_id") or "").strip(),
            )
            in keys
        ]

    from backtest_pipeline.src.vectorbt_adapter import _npz_candidates_for_event
    from data_system.src.event_data_resolver import npz_search_dirs

    search_dirs = npz_search_dirs(repo_root)
    kept: List[Dict[str, Any]] = []
    for unit in units:
        event_id = str(unit.get("event_id") or "").strip()
        symbol = unit.get("symbol")
        if event_id:
            if _npz_candidates_for_event(search_dirs, event_id, symbol):
                kept.append(unit)
    return kept


def write_units_jsonl(path: Path, units: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for unit in units:
            handle.write(json.dumps(unit, sort_keys=True) + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate VectorBT paid-screen unit JSONL")
    parser.add_argument("--out", type=Path, required=True, help="Output JSONL path")
    parser.add_argument(
        "--events-csv",
        type=Path,
        default=_REPO / "packages" / "data_system" / "config" / "events.csv",
    )
    parser.add_argument("--model-id", default="SPREAD_BLOWOUT_RECOMPRESSION")
    parser.add_argument("--symbols", default=CME_M6_SYMBOLS, help="Comma-separated symbols (default: CME M6 universe)")
    parser.add_argument("--event-types", default=None, help="Comma-separated event_type filter")
    parser.add_argument("--window-name", default="TIGHT")
    parser.add_argument("--smoke-count", type=int, default=None, help="Cap events for smoke JSONL")
    parser.add_argument(
        "--from-stage-a-survivors",
        type=Path,
        default=None,
        help="Expand stage_a_survivors.json into survivor-scoped units (current Vast full default)",
    )
    parser.add_argument(
        "--all-active-models",
        action="store_true",
        help="Expand all active hypotheses across eligible TIGHT events (explicit legacy/exploratory mode)",
    )
    parser.add_argument(
        "--model-ids",
        default=None,
        help="Comma-separated model slugs or legacy ids (alternative to --all-active-models)",
    )
    parser.add_argument("--max-units", type=int, default=None)
    parser.add_argument("--thesis-template", default=_DEFAULT_THESIS_TEMPLATE)
    parser.add_argument(
        "--research-split",
        choices=sorted(RESEARCH_SPLIT_CHOICES.keys()),
        default=None,
        help=(
            "Walk-forward scope for event release_date filtering. "
            f"Default for --all-active-models: {DEFAULT_ALL_ACTIVE_RESEARCH_SPLIT} "
            "(Discovery+Confirmation; holdout/recent excluded unless explicit)."
        ),
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="Inclusive release_date lower bound (YYYY-MM-DD); overrides period names when set",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Inclusive release_date upper bound (YYYY-MM-DD); overrides period names when set",
    )
    parser.add_argument(
        "--validation-cpi-first",
        action="store_true",
        help="Sort JSONL with CPI event_type rows first (validation runs only; not Phase D scope)",
    )
    parser.add_argument(
        "--require-runnable-npz",
        action="store_true",
        help="Drop units with no NPZ at HFT3_NPZ_ROOT (Vast full default via VBT_REQUIRE_RUNNABLE_NPZ=1)",
    )
    parser.add_argument(
        "--research-clock",
        default=RESEARCH_CLOCK_SCHEDULED_EVENT,
        help="Research clock for generated units (default: scheduled_event)",
    )
    parser.add_argument(
        "--context-set-id",
        default=TARGET_ONLY_CONTEXT_SET_ID,
        help="Allowed context set for generated units (default: target_only)",
    )
    parser.add_argument(
        "--declared-context-sets",
        default=None,
        help="Comma-separated context sets declared by generated units; defaults to target_only plus context-set-id",
    )
    parser.add_argument(
        "--ablation-group-id",
        default=None,
        help="Optional ablation group identity; included in non-baseline unit IDs",
    )
    parser.add_argument(
        "--negative-control-policy-json",
        default=None,
        help="Optional JSON object overriding the default negative-control policy stamp",
    )
    args = parser.parse_args(argv)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    event_types: Optional[Set[str]] = None
    if args.event_types:
        event_types = {t.strip() for t in args.event_types.split(",") if t.strip()}

    try:
        research_clock = validate_research_clock(args.research_clock)
        context_set_id = _normalize_context_set_id(args.context_set_id)
        declared_context_sets = _parse_declared_context_sets(args.declared_context_sets, context_set_id)
        negative_control_policy = _parse_negative_control_policy(
            args.negative_control_policy_json,
            research_clock=research_clock,
            context_set_id=context_set_id,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    research_split = args.research_split
    if (args.all_active_models or args.model_ids) and research_split is None:
        if not args.start_date and not args.end_date:
            research_split = DEFAULT_ALL_ACTIVE_RESEARCH_SPLIT

    split_start, split_end, split_label = _resolve_split_date_bounds(
        research_split,
        start_date=args.start_date,
        end_date=args.end_date,
    )

    if args.from_stage_a_survivors:
        units = _units_from_stage_a_survivors(
            args.from_stage_a_survivors,
            args.events_csv,
            symbols=symbols,
            event_types=event_types,
            thesis_template=args.thesis_template,
            max_units=args.max_units,
            window_name=args.window_name,
            start_date=split_start,
            end_date=split_end,
            research_split=split_label,
            research_clock=research_clock,
            context_set_id=context_set_id,
            declared_context_sets=declared_context_sets,
            ablation_group_id=args.ablation_group_id,
            negative_control_policy=negative_control_policy,
        )
    elif args.all_active_models or args.model_ids:
        model_id_list: Optional[List[str]] = None
        if args.model_ids:
            model_id_list = [m.strip() for m in args.model_ids.split(",") if m.strip()]
        units = _units_from_all_active_models(
            args.events_csv,
            symbols=symbols,
            event_types=event_types,
            thesis_template=args.thesis_template,
            window_name=args.window_name,
            max_units=args.max_units,
            model_ids=model_id_list,
            start_date=args.start_date,
            end_date=args.end_date,
            research_split=research_split,
            require_runnable_npz=args.require_runnable_npz,
            repo_root=_REPO,
            research_clock=research_clock,
            context_set_id=context_set_id,
            declared_context_sets=declared_context_sets,
            ablation_group_id=args.ablation_group_id,
            negative_control_policy=negative_control_policy,
        )
    else:
        max_rows = args.smoke_count or args.max_units
        events = _load_events(
            args.events_csv,
            event_types=event_types,
            symbols=symbols,
            window_name=args.window_name,
            max_rows=max_rows,
            start_date=split_start,
            end_date=split_end,
        )
        resolved_model_id = resolve_model_id(args.model_id)
        units = _units_from_events(
            events,
            model_id=resolved_model_id,
            thesis_template=args.thesis_template,
            research_split=split_label,
            research_clock=research_clock,
            context_set_id=context_set_id,
            declared_context_sets=declared_context_sets,
            ablation_group_id=args.ablation_group_id,
            negative_control_policy=negative_control_policy,
        )
        enriched: List[Dict[str, Any]] = []
        for unit in units:
            row = dict(unit)
            try:
                row["hyp_id"] = get_hyp_id_for_slug(resolved_model_id)
            except KeyError:
                pass
            enriched.append(row)
        units = enriched
        if args.max_units is not None:
            units = units[: args.max_units]

    if args.require_runnable_npz:
        before = len(units)
        units = _filter_runnable_npz_units(units, _REPO)
        dropped = before - len(units)
        if dropped:
            print(f"Filtered {dropped} units without runnable NPZ ({len(units)} remain)")

    if not units:
        print("ERROR: zero units generated", file=sys.stderr)
        return 1

    if args.validation_cpi_first:
        units.sort(
            key=lambda u: (
                0 if str(u.get("event_type") or "").upper() == "CPI" else 1,
                str(u.get("event_id") or ""),
            )
        )

    out = args.out if args.out.is_absolute() else _REPO / args.out
    write_units_jsonl(out, units)
    print(f"Wrote {len(units)} units to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
