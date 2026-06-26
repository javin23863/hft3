"""NPZ / OHLCV data-quality checks for the research pipeline (Phase 0)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

_REQUIRED_NPZ_FIELDS = frozenset({"ev", "local_ts", "px", "qty", "order_id"})
_MIN_OHLCV_EVENTS = 2


class DataQualityError(Exception):
    """Base for data-quality failures — not model evaluation failures."""


class NoOHLCVDataError(DataQualityError):
    """NPZ is present but cannot produce OHLCV bars for screening."""


@dataclass(frozen=True)
class NpzOhlcvCheckResult:
    valid: bool
    reason: str | None = None
    event_count: int | None = None
    npz_path: str | None = None


def check_npz_ohlcv(path: Path | str) -> NpzOhlcvCheckResult:
    """Return whether *path* can supply OHLCV bars (>=2 MBO events, required fields)."""
    npz_path = Path(path)
    if not npz_path.is_file():
        return NpzOhlcvCheckResult(
            valid=False,
            reason="missing_npz",
            npz_path=str(npz_path),
        )
    try:
        import numpy as np

        with np.load(npz_path, allow_pickle=False) as archive:
            if "data" not in archive:
                return NpzOhlcvCheckResult(
                    valid=False,
                    reason="missing_data_array",
                    npz_path=str(npz_path),
                )
            raw = archive["data"]
    except Exception as exc:  # noqa: BLE001 — surface load failures as DQ reasons
        return NpzOhlcvCheckResult(
            valid=False,
            reason=f"npz_load_error:{exc}",
            npz_path=str(npz_path),
        )

    if raw.dtype.names is None:
        return NpzOhlcvCheckResult(
            valid=False,
            reason="unstructured_data_array",
            npz_path=str(npz_path),
        )
    missing = _REQUIRED_NPZ_FIELDS - set(raw.dtype.names)
    if missing:
        return NpzOhlcvCheckResult(
            valid=False,
            reason=f"missing_fields:{','.join(sorted(missing))}",
            event_count=len(raw),
            npz_path=str(npz_path),
        )
    event_count = len(raw)
    if event_count < _MIN_OHLCV_EVENTS:
        return NpzOhlcvCheckResult(
            valid=False,
            reason="insufficient_events",
            event_count=event_count,
            npz_path=str(npz_path),
        )
    return NpzOhlcvCheckResult(
        valid=True,
        reason=None,
        event_count=event_count,
        npz_path=str(npz_path),
    )


def is_no_ohlcv_error(exc: BaseException | str | None) -> bool:
    if exc is None:
        return False
    if isinstance(exc, NoOHLCVDataError):
        return True
    text = str(exc).lower()
    return "no_ohlcv_data" in text or "insufficient_events" in text


def classify_evaluation_error(exc: BaseException) -> tuple[str | None, str]:
    """Return (failure_class, message). Data-quality failures are not model failures."""
    if isinstance(exc, DataQualityError):
        return "data_quality", str(exc)
    if is_no_ohlcv_error(exc):
        return "data_quality", str(exc)
    return "model", str(exc)


def load_skip_bad_units_payload(path: Path) -> dict[str, Any]:
    """Load JSON skip report from ``scripts/check_lake_data.py``."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"skip file must be a JSON object: {path}")
    return payload


def skipped_unit_id_set(
    *,
    skip_bad_units_file: Path | None = None,
    skipped_unit_ids: list[str] | None = None,
) -> set[str]:
    """Union inline skip ids with ``invalid_unit_ids`` keys from a skip report file."""
    out: set[str] = set(skipped_unit_ids or [])
    if skip_bad_units_file is not None and skip_bad_units_file.is_file():
        payload = load_skip_bad_units_payload(skip_bad_units_file)
        invalid = payload.get("invalid_unit_ids") or {}
        if isinstance(invalid, dict):
            out.update(str(k) for k in invalid)
        elif isinstance(invalid, list):
            out.update(str(k) for k in invalid)
    return out


def unit_matches_skip(
    unit_id: str,
    *,
    symbol: str | None = None,
    event_id: str | None = None,
    skip_ids: set[str],
) -> bool:
    """Match unit_id or symbol/event composite keys used in skip lists."""
    if unit_id in skip_ids:
        return True
    if symbol and event_id:
        composite = f"{symbol}_{event_id}"
        if composite in skip_ids:
            return True
        if f"{symbol}|{event_id}" in skip_ids:
            return True
    return False


def abort_on_failed_units_for_scope(scope: str, config: Mapping[str, Any]) -> bool:
    """Resolve abort policy: per-scope map overrides legacy boolean default."""
    by_scope = config.get("abort_on_failed_units_by_scope")
    if isinstance(by_scope, dict):
        key = scope.replace("_", "-")
        if key in by_scope:
            return bool(by_scope[key])
        if scope in by_scope:
            return bool(by_scope[scope])
    legacy = config.get("abort_on_failed_units")
    if legacy is not None:
        return bool(legacy)
    broad_scopes = {
        "paid",
        "paid-compute",
        "paid_compute",
        "broad",
        "broad-screen",
        "broad_screen",
        "full_lake",
        "continuous_full_cme",
    }
    normalized = scope.replace("_", "-")
    if scope in broad_scopes or normalized in broad_scopes:
        return False
    return True
