"""NPZ / OHLCV data-quality checks for the research pipeline (Phase 0).

Validates that NPZ files contain the data needed to produce OHLCV bars for
backtesting. Supports two layouts: (1) MBO event array (hft3 lake ``data``
member with fields {ev, local_ts, px, qty, order_id}); (2) pre-built OHLCV
arrays (open/high/low/close/volume).

``check_npz_ohlcv`` returns an ``NpzOhlcvCheckResult`` that is also tuple-
unpackable as ``(valid, reason)`` for backward compatibility with callers
that do ``ok, reason = check_npz_ohlcv(path)``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

_REQUIRED_NPZ_FIELDS = frozenset({"ev", "local_ts", "px", "qty", "order_id"})
_MIN_OHLCV_EVENTS = 2
_OHLCV_KEYS = ("open", "high", "low", "close", "volume")


class DataQualityError(ValueError):
    """Base for data-quality failures — not model evaluation failures.

    Inherits ValueError so callers that catch ``ValueError`` (e.g.
    ``features_engine.src.features.npz_feed``) continue to work.
    """


class NoOHLCVDataError(DataQualityError):
    """NPZ is present but cannot produce OHLCV bars for screening."""


@dataclass(frozen=True)
class NpzOhlcvCheckResult:
    valid: bool
    reason: str | None = None
    event_count: int | None = None
    npz_path: str | None = None

    def __iter__(self):
        yield self.valid
        yield self.reason or ""

    def __getitem__(self, index):
        return (self.valid, self.reason or "")[index]


def check_npz_ohlcv(path: Path | str) -> NpzOhlcvCheckResult:
    """Return whether *path* can supply OHLCV bars.

    Supports MBO-event-array (layout 1) and pre-built OHLCV (layout 2).
    Returns ``NpzOhlcvCheckResult`` — also tuple-unpackable as
    ``(valid, reason)`` for legacy callers.
    """
    npz_path = Path(path)
    if not npz_path.is_file():
        return NpzOhlcvCheckResult(
            valid=False, reason="missing_npz", npz_path=str(npz_path),
        )
    try:
        import numpy as np

        with np.load(npz_path, allow_pickle=False) as archive:
            if all(k in archive for k in _OHLCV_KEYS):
                for k in _OHLCV_KEYS:
                    arr = archive[k]
                    if arr.size == 0:
                        return NpzOhlcvCheckResult(
                            valid=False, reason=f"no_ohlcv_data: empty {k} array",
                            npz_path=str(npz_path),
                        )
                return NpzOhlcvCheckResult(valid=True, reason=None, npz_path=str(npz_path))

            if "data" not in archive:
                return NpzOhlcvCheckResult(
                    valid=False, reason="missing_data_array",
                    npz_path=str(npz_path),
                )
            raw = archive["data"]
    except (OSError, ValueError, KeyError) as exc:
        raise NoOHLCVDataError(f"{npz_path}: {type(exc).__name__}: {exc}") from exc
    except Exception as exc:
        return NpzOhlcvCheckResult(
            valid=False, reason=f"npz_load_error:{exc}", npz_path=str(npz_path),
        )

    if raw.dtype.names is None:
        return NpzOhlcvCheckResult(
            valid=False, reason="unstructured_data_array", npz_path=str(npz_path),
        )
    missing = _REQUIRED_NPZ_FIELDS - set(raw.dtype.names)
    if missing:
        return NpzOhlcvCheckResult(
            valid=False, reason=f"missing_fields:{','.join(sorted(missing))}",
            event_count=len(raw), npz_path=str(npz_path),
        )
    event_count = len(raw)
    if event_count < _MIN_OHLCV_EVENTS:
        return NpzOhlcvCheckResult(
            valid=False, reason="insufficient_events",
            event_count=event_count, npz_path=str(npz_path),
        )
    derivability_reason = _npz_ohlcv_derivability_reason(raw)
    if derivability_reason is not None:
        return NpzOhlcvCheckResult(
            valid=False, reason=derivability_reason, event_count=event_count,
            npz_path=str(npz_path),
        )
    return NpzOhlcvCheckResult(
        valid=True, reason=None, event_count=event_count, npz_path=str(npz_path),
    )


def _npz_ohlcv_derivability_reason(raw: Any) -> str | None:
    """Cheap sample checks mirroring ``_default_data_loader`` bar prerequisites."""
    import numpy as np

    try:
        px = np.asarray(raw["px"], dtype=np.float64)
        ts = np.asarray(raw["local_ts"], dtype=np.int64)
    except (KeyError, TypeError, ValueError):
        return "ohlcv_derivability_error:missing_px_or_ts"
    if px.size == 0 or ts.size == 0:
        return "ohlcv_derivability_error:empty_px_or_ts"
    if not np.isfinite(px).any() or float(np.nanmax(px)) <= 0.0:
        return "ohlcv_derivability_error:non_positive_px"
    if ts.size >= 2 and not bool(np.all(ts[1:] >= ts[:-1])):
        return "ohlcv_derivability_error:non_monotonic_local_ts"
    return None


_NO_OHLCV_ERROR_TOKENS = frozenset({"no_ohlcv_data", "no ohlcv data"})


def is_no_ohlcv_error(exc: BaseException | str | None) -> bool:
    if exc is None:
        return False
    if isinstance(exc, NoOHLCVDataError):
        return True
    text = str(exc).strip().lower()
    return text in _NO_OHLCV_ERROR_TOKENS or text.startswith("no_ohlcv_data:")


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
        "paid", "paid-compute", "paid_compute", "broad", "broad-screen",
        "broad_screen", "full_lake", "continuous_full_cme",
    }
    normalized = scope.replace("_", "-")
    if scope in broad_scopes or normalized in broad_scopes:
        return False
    return True