"""Precomputed sensor feature adapter (e.g. VIX options features)."""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

_AUDIT_COLS = {"ts_event_raw", "ts_recv_raw"}


class PrecomputedFeatureAdapter:
    """Duck-type peer of HistoricalReplayMarketDataAdapter for precomputed npz files.

    Expected npz layout (matches vix_features.py output):
      - ``ts``            : int64 array, availability timestamps (ns)
      - feature columns   : float64 arrays (names listed in attrs['columns'])
      - ``ts_event_raw``  : int64 audit column (skipped)
      - ``ts_recv_raw``   : int64 audit column (skipped)
      - ``_attrs_json``   : object array([str(attrs_dict)]) — keys include
                            'columns' (list[str]) and 'columns_omitted' (list[str])

    NaN values are preserved as-is; consumers guard them per hypothesis spec.
    """

    def __init__(self, npz_path: str | Path) -> None:
        data = np.load(str(npz_path), allow_pickle=True)

        self._ts: np.ndarray = data["ts"].astype(np.int64)

        # Parse attrs to discover present feature columns.
        columns: Optional[List[str]] = None
        if "_attrs_json" in data:
            raw = data["_attrs_json"]
            text = str(raw[0]) if raw.ndim > 0 else str(raw)
            # vix_features.py stores str(dict), not json.dumps — try both.
            try:
                attrs = json.loads(text)
            except json.JSONDecodeError:
                attrs = ast.literal_eval(text)
            columns = attrs.get("columns")

        if columns is not None:
            self._columns: List[str] = [c for c in columns if c not in _AUDIT_COLS]
        else:
            # Fallback: every non-ts, non-audit key.
            self._columns = [
                k for k in data.files
                if k not in ("ts", "_attrs_json") and k not in _AUDIT_COLS
            ]

        self._arrays: Dict[str, np.ndarray] = {
            col: data[col].astype(np.float64)
            for col in self._columns
            if col in data
        }
        self._row: int = -1

    def sync_to_timestamp(self, ts_ns: int) -> None:
        """Set internal cursor to the latest row whose ts <= ts_ns."""
        self._row = int(np.searchsorted(self._ts, ts_ns, side="right")) - 1

    def current_timestamp_ns(self) -> int | None:
        """Availability timestamp of the current row, or None before first sample."""
        if self._row < 0:
            return None
        return int(self._ts[self._row])

    def current_features(self) -> Dict[str, float] | None:
        """Return {column: value} at current row, or None before first sample."""
        if self._row < 0:
            return None
        row = self._row
        return {col: float(arr[row]) for col, arr in self._arrays.items()}
