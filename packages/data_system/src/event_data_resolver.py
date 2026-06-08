"""Unified event-slot asset resolution (MBO NPZ, raw lane, VIX sensor)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Literal, Optional, Tuple

from data_system.src.data_roots import npz_search_dirs, paid_data_root
from data_system.src.npz_resolver import resolve_npz_for_event

VIX_OPT_SYMBOL = "VIX.OPT"
CMBP1_START = datetime(2023, 3, 28, tzinfo=timezone.utc)

SlotStatus = Literal[
    "complete",
    "derivable",
    "invalid",
    "pre_cutoff",
    "not_downloaded",
    "skipped_pre_cmbp1",
]


def sensor_filename(event_id: str) -> str:
    return f"{event_id}_sensors.parquet"


def sensor_search_dirs(repo_root: Path) -> List[Path]:
    root = repo_root.resolve()
    dirs: List[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        key = str(path.resolve()).lower()
        if key in seen:
            return
        seen.add(key)
        dirs.append(path.resolve())

    _add(root / "data" / "sensors")
    paid = paid_data_root(root)
    _add(paid / "sensors")
    return dirs


def mbo_release_search_roots(repo_root: Path) -> List[Path]:
    root = repo_root.resolve()
    dirs: List[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        key = str(path.resolve()).lower()
        if key in seen:
            return
        seen.add(key)
        dirs.append(path.resolve())

    _add(root / "data" / "mbo_release")
    paid = paid_data_root(root)
    _add(paid / "mbo_release")
    return dirs


def _legacy_vix_raw_path(repo_root: Path, event_id: str) -> Path:
    return repo_root / "data" / "vix_options" / "cmbp1" / event_id.replace("/", "_") / "raw.dbn.zst"


def _find_file_in_roots(roots: List[Path], *parts: str) -> Optional[Path]:
    for root in roots:
        candidate = root.joinpath(*parts)
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _sensor_parquet_has_finite_level(path: Path) -> bool:
    """True when parquet exists and at least one row has a finite level."""
    try:
        import pyarrow.parquet as pq

        table = pq.read_table(path, columns=["level"])
        if table.num_rows == 0:
            return False
        col = table.column("level").to_pylist()
        return any(v == v and v is not None for v in col)
    except Exception:
        return False


def resolve_sensor_for_event(repo_root: Path, event_id: str) -> Tuple[Path, bool]:
    name = sensor_filename(event_id)
    for d in sensor_search_dirs(repo_root):
        hit = d / name
        if hit.is_file() and hit.stat().st_size > 0 and _sensor_parquet_has_finite_level(hit):
            return hit, True
    return repo_root / "data" / "sensors" / name, False


def resolve_mbo_npz_for_event(
    repo_root: Path,
    event_id: str,
    symbol: str,
    parsed_symbols: tuple[str, ...],
) -> Tuple[Path, bool, str]:
    """Canonical MBO NPZ resolution entry point for backtest and pipelines."""
    return resolve_npz_for_event(repo_root, event_id, symbol, parsed_symbols)


def resolve_vix_raw_for_event(repo_root: Path, event_id: str) -> Tuple[Path, bool]:
    from mbo_release_lane.storage import raw_dbn_path, release_slot_dir, validate_dbn_readable

    for root in mbo_release_search_roots(repo_root):
        slot = root / event_id / VIX_OPT_SYMBOL.replace("/", "_")
        raw = raw_dbn_path(slot)
        if raw.is_file() and raw.stat().st_size > 0:
            return raw, validate_dbn_readable(raw)

    legacy = _legacy_vix_raw_path(repo_root, event_id)
    if legacy.is_file() and legacy.stat().st_size > 0:
        return legacy, validate_dbn_readable(legacy)
    slot = release_slot_dir(repo_root, event_id, VIX_OPT_SYMBOL)
    return raw_dbn_path(slot), False


def resolve_mbo_raw_for_event(
    repo_root: Path, event_id: str, symbol: str
) -> Tuple[Path, bool, Optional[str]]:
    from mbo_release_lane.storage import (
        load_release_event_path,
        raw_dbn_path,
        release_slot_dir,
        validate_dbn_readable,
    )

    validation: Optional[str] = None
    for root in mbo_release_search_roots(repo_root):
        slot = root / event_id / symbol.replace("/", "_")
        raw = raw_dbn_path(slot)
        if raw.is_file() and raw.stat().st_size > 0:
            manifest = load_release_event_path(slot)
            if manifest:
                validation = manifest.get("release_event_path", {}).get("validation_status")
            readable = validate_dbn_readable(raw)
            return raw, readable, validation

    slot = release_slot_dir(repo_root, event_id, symbol)
    return raw_dbn_path(slot), False, validation


@dataclass(frozen=True)
class MboSlotStatus:
    event_id: str
    symbol: str
    raw_ok: bool
    npz_ok: bool
    validation_status: Optional[str]
    status: SlotStatus
    raw_path: str
    npz_path: str
    source_vendor: str = "unknown"


@dataclass(frozen=True)
class VixSlotStatus:
    event_id: str
    raw_ok: bool
    sensor_ok: bool
    status: SlotStatus
    raw_path: str
    sensor_path: str


def _mbo_slot_status(
    repo_root: Path,
    event_id: str,
    symbol: str,
    parsed_symbols: tuple[str, ...],
) -> MboSlotStatus:
    from mbo_release_lane.npz_adapter import has_deriveable_mbo, is_release_valid

    raw_path, raw_ok, validation = resolve_mbo_raw_for_event(repo_root, event_id, symbol)
    npz_path, npz_ok, _ = resolve_npz_for_event(repo_root, event_id, symbol, parsed_symbols)
    raw_present = raw_path.is_file() and raw_path.stat().st_size > 0

    if npz_ok:
        status: SlotStatus = "complete"
    elif not raw_present:
        status = "not_downloaded"
    elif not raw_ok:
        status = "invalid"
    elif validation == "valid" or has_deriveable_mbo(repo_root, event_id, symbol):
        status = "derivable"
    elif validation and validation != "valid":
        status = "invalid"
    elif not is_release_valid(repo_root, event_id, symbol):
        status = "invalid"
    else:
        status = "derivable"

    # Read the per-slot source vendor (databento / rithmic_api) so the
    # audit can report slot-level provenance.  A Rithmic-sourced slot
    # with NPZ already derived looks identical to a Databento one at
    # the NPZ layer; the source_vendor field is the only way to
    # distinguish.  Default "unknown" when the manifest is missing.
    # Read the manifest directly — do not gate on validation status,
    # because Rithmic slots have no raw DBN file and therefore return
    # validation=None through resolve_mbo_raw_for_event even when the
    # slot itself is valid.
    source_vendor: str = "unknown"
    from mbo_release_lane.storage import load_release_event_path, release_slot_dir

    for root_search in mbo_release_search_roots(repo_root):
        slot = root_search / event_id / symbol.replace("/", "_")
        if slot.is_dir():
            rep = load_release_event_path(slot)
            if rep:
                source_vendor = str(
                    rep.get("release_event_path", {}).get("source_vendor", "unknown")
                )
            break

    return MboSlotStatus(
        event_id=event_id,
        symbol=symbol,
        raw_ok=raw_ok,
        npz_ok=npz_ok,
        validation_status=validation,
        status=status,
        source_vendor=source_vendor,
        raw_path=str(raw_path),
        npz_path=str(npz_path),
    )


def _vix_slot_status(repo_root: Path, event_id: str, window_start_utc: Any) -> VixSlotStatus:
    from mbo_release_lane.storage import dbn_has_quote_records

    raw_path, raw_ok = resolve_vix_raw_for_event(repo_root, event_id)
    sensor_path, sensor_ok = resolve_sensor_for_event(repo_root, event_id)

    start = window_start_utc
    if hasattr(start, "to_pydatetime"):
        start = start.to_pydatetime()
    if isinstance(start, str):
        start = datetime.fromisoformat(start.replace("Z", "+00:00"))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)

    raw_present = raw_path.is_file() and raw_path.stat().st_size > 0

    if start < CMBP1_START:
        status: SlotStatus = "skipped_pre_cmbp1"
    elif sensor_ok:
        status = "complete"
    elif raw_ok and dbn_has_quote_records(raw_path):
        status = "derivable"
    elif raw_present:
        status = "invalid"
    else:
        status = "not_downloaded"

    return VixSlotStatus(
        event_id=event_id,
        raw_ok=raw_ok,
        sensor_ok=sensor_ok,
        status=status,
        raw_path=str(raw_path),
        sensor_path=str(sensor_path),
    )


def resolve_event_assets(
    repo_root: Path,
    event_id: str,
    symbol: str,
    parsed_symbols: tuple[str, ...] | None = None,
    *,
    window_start_utc: Any = None,
) -> dict[str, Any]:
    """Return unified asset map for one event (MBO symbol + optional VIX)."""
    parsed = parsed_symbols or (symbol,)
    npz_path, npz_present, sym_used = resolve_npz_for_event(repo_root, event_id, symbol, parsed)
    raw_path, raw_present, validation = resolve_mbo_raw_for_event(repo_root, event_id, symbol)
    vix_raw, vix_raw_present = resolve_vix_raw_for_event(repo_root, event_id)
    sensor_path, sensor_present = resolve_sensor_for_event(repo_root, event_id)

    vix_status = None
    if window_start_utc is not None:
        vix_status = _vix_slot_status(repo_root, event_id, window_start_utc)

    return {
        "event_id": event_id,
        "symbol": symbol,
        "mbo_npz": {"path": str(npz_path), "present": npz_present, "symbol_used": sym_used},
        "mbo_raw": {
            "path": str(raw_path),
            "present": raw_present,
            "validation_status": validation,
        },
        "vix_raw": {"path": str(vix_raw), "present": vix_raw_present},
        "vix_sensor": {"path": str(sensor_path), "present": sensor_present},
        "vix_status": vix_status.status if vix_status else None,
        "search_dirs": {
            "npz": [str(d) for d in npz_search_dirs(repo_root)],
            "sensors": [str(d) for d in sensor_search_dirs(repo_root)],
            "mbo_release": [str(d) for d in mbo_release_search_roots(repo_root)],
        },
    }


def load_sensor_df(repo_root: Path, event_id: str):
    """Load sensor parquet as DataFrame, or empty DataFrame if missing."""
    import pandas as pd

    path, present = resolve_sensor_for_event(repo_root, event_id)
    if not present:
        return pd.DataFrame(columns=["event_id", "offset_sec", "sensor", "level", "ts_ns"])
    return pd.read_parquet(path)


def build_priority_lane_coverage(repo_root: Path) -> dict:
    """Full priority-lane inventory for MBO + VIX.OPT."""
    from collections import Counter
    from datetime import datetime, timezone

    from economic_event_universe.events_csv_builder import resolve_download_scope_windows
    from economic_event_universe.registry import default_cme_symbols
    from mbo_release_lane.constants import PRIORITY_DOWNLOAD_EVENT_TYPES
    from mbo_release_lane.download import filter_windows_by_event_type, resolve_download_exclusions

    windows = resolve_download_scope_windows(repo_root, "macro_releases")
    windows = filter_windows_by_event_type(
        windows, exclude_event_types=resolve_download_exclusions()
    )
    windows = [w for w in windows if w.event_type in PRIORITY_DOWNLOAD_EVENT_TYPES]
    syms = default_cme_symbols()

    mbo_slots: list[dict] = []
    vix_slots: list[dict] = []
    mbo_status_counts: Counter = Counter()
    vix_status_counts: Counter = Counter()
    mbo_source_vendor_counts: Counter = Counter()

    def _as_dt(value):
        if hasattr(value, "to_pydatetime"):
            return value.to_pydatetime()
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    for w in windows:
        parsed = syms
        for sym in syms:
            st = _mbo_slot_status(repo_root, w.event_id, sym, parsed)
            mbo_status_counts[st.status] += 1
            mbo_source_vendor_counts[st.source_vendor] += 1
            if st.status != "complete":
                mbo_slots.append(
                    {
                        "event_id": st.event_id,
                        "symbol": st.symbol,
                        "status": st.status,
                        "raw_ok": st.raw_ok,
                        "npz_ok": st.npz_ok,
                        "validation_status": st.validation_status,
                        "source_vendor": st.source_vendor,
                    }
                )

        vst = _vix_slot_status(repo_root, w.event_id, w.start_utc)
        vix_status_counts[vst.status] += 1
        if vst.status not in ("complete", "skipped_pre_cmbp1"):
            vix_slots.append(
                {
                    "event_id": vst.event_id,
                    "status": vst.status,
                    "raw_ok": vst.raw_ok,
                    "sensor_ok": vst.sensor_ok,
                }
            )

    total_mbo = len(windows) * len(syms)
    mbo_complete = mbo_status_counts.get("complete", 0)
    vix_eligible = sum(1 for w in windows if _as_dt(w.start_utc) >= CMBP1_START)
    vix_complete = vix_status_counts.get("complete", 0)
    vix_skipped = vix_status_counts.get("skipped_pre_cmbp1", 0)

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "macro_releases_priority",
        "event_types": list(PRIORITY_DOWNLOAD_EVENT_TYPES),
        "window_count": len(windows),
        "cme_symbols": list(syms),
        "vix_symbol": VIX_OPT_SYMBOL,
        "cmbp1_start_utc": CMBP1_START.isoformat(),
        "mbo": {
            "total_slots": total_mbo,
            "complete": mbo_complete,
            "complete_pct": round(100.0 * mbo_complete / max(total_mbo, 1), 2),
            "status_counts": dict(mbo_status_counts),
            "source_vendor_counts": dict(mbo_source_vendor_counts),
            "incomplete_sample": mbo_slots[:100],
            "incomplete_total": len(mbo_slots),
        },
        "vix": {
            "total_windows": len(windows),
            "eligible_post_cutoff": vix_eligible,
            "skipped_pre_cmbp1": vix_skipped,
            "complete": vix_complete,
            "complete_pct_eligible": round(100.0 * vix_complete / max(vix_eligible, 1), 2),
            "status_counts": dict(vix_status_counts),
            "incomplete_sample": vix_slots[:100],
            "incomplete_total": len(vix_slots),
            "vvix_note": "VVIX index unavailable on Databento — not a local gap",
        },
    }
