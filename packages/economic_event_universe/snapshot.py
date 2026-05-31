"""Cross-asset L3 snapshot provider delegating to hfc3 tensor builder."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

import pandas as pd

from data_system.src.events_parser import load_and_parse_events
from data_system.src.npz_resolver import resolve_npz_for_event
from economic_event_universe.registry import get_event_def
from economic_event_universe.windows import snapshot_offsets
from hfc3.events.l3_event_snapshot_tensor import build_l3_event_tensor
from hft3_bootstrap import data_system_root, repo_root, workbench_root


def _sensor_symbols(repo: Path) -> list[str]:
    import yaml

    hot_path = workbench_root(repo) / "config" / "hot_memory_universe.yaml"
    if not hot_path.is_file():
        return []
    raw = yaml.safe_load(hot_path.read_text(encoding="utf-8")) or {}
    out: list[str] = []
    for inst in raw.get("instruments") or []:
        if str(inst.get("hot_memory_tier", "")) == "HOT_SENSOR":
            sym = str(inst.get("research_symbol", "")).strip()
            if sym:
                out.append(sym)
    return out


@dataclass
class SnapshotFrame:
    event_id: str
    offset_sec: int
    symbols: tuple[str, ...]
    rows: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class CrossAssetSnapshotProvider(Protocol):
    def collect(
        self,
        event_id: str,
        offset_sec: int,
        symbols: Sequence[str],
    ) -> SnapshotFrame: ...


class DefaultSnapshotProvider:
    """Offline research provider; not on CHI404 hot path (BLUEPRINT §4)."""

    def __init__(self, repo: Path | None = None, *, include_sensors: bool = True):
        self.repo = repo or repo_root()
        self.include_sensors = include_sensors
        self._events_csv = data_system_root(self.repo) / "config" / "events.csv"

    def collect(
        self,
        event_id: str,
        offset_sec: int,
        symbols: Sequence[str],
    ) -> SnapshotFrame:
        df = load_and_parse_events(str(self._events_csv))
        row = df[df["event_id"] == event_id]
        if row.empty:
            raise ValueError(f"event_id not in events.csv: {event_id}")
        r = row.iloc[0]
        et = str(r["event_type"])
        parsed = [x.strip() for x in str(r["symbols"]).split(",") if x.strip()]
        sym_list = list(symbols) if symbols else list(parsed)
        if self.include_sensors:
            for s in _sensor_symbols(self.repo):
                if s not in sym_list:
                    sym_list.append(s)
        mbo_missing: list[str] = []
        for sym in sym_list:
            _, present, _ = resolve_npz_for_event(self.repo, event_id, sym, parsed)
            if not present:
                mbo_missing.append(sym)

        offs = snapshot_offsets(et)
        if offset_sec not in offs:
            offs = tuple(sorted(set(offs) | {int(offset_sec)}))

        tensor_df = build_l3_event_tensor(
            self.repo,
            event_id,
            symbols=sym_list,
            offsets_sec=offs,
        )
        sub = tensor_df[tensor_df["offset_sec"] == int(offset_sec)]
        cfg = get_event_def(et)
        meta = {
            "data_source": "databento_npz",
            "mbo_missing": mbo_missing,
            "source_url": str(r.get("source_url", cfg.get("official_source_url", ""))),
            "event_type": et,
            "offset_sec": int(offset_sec),
            "include_sensors": self.include_sensors,
        }
        return SnapshotFrame(
            event_id=event_id,
            offset_sec=int(offset_sec),
            symbols=tuple(sym_list),
            rows=sub.to_dict("records"),
            metadata=meta,
        )

    def collect_all_offsets(
        self,
        event_id: str,
        symbols: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        df = load_and_parse_events(str(self._events_csv))
        row = df[df["event_id"] == event_id]
        if row.empty:
            raise ValueError(f"event_id not in events.csv: {event_id}")
        et = str(row.iloc[0]["event_type"])
        parsed = [x.strip() for x in str(row.iloc[0]["symbols"]).split(",") if x.strip()]
        sym_list = list(symbols) if symbols else list(parsed)
        if self.include_sensors:
            for s in _sensor_symbols(self.repo):
                if s not in sym_list:
                    sym_list.append(s)
        return build_l3_event_tensor(
            self.repo,
            event_id,
            symbols=sym_list,
            offsets_sec=snapshot_offsets(et),
        )
