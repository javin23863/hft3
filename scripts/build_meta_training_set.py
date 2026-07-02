#!/usr/bin/env python
"""Build meta-label training tables from prepared event tapes.

Pass A of the two-pass ML design: for each (model, tape) the same
feature/signal precompute the HBT lane uses produces per-timestamp
64-slot features, the primary signal, and the mid price; triple-barrier
outcomes (Lopez de Prado, AFML ch.3) labeled with sides = sign(primary
signal) become meta labels (1 = signal fired and hit the profit barrier).
Receipts record row counts and the ordered feature schema; training and
evaluation splits happen downstream in train_meta_models.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "packages"), str(REPO / "apps")]

SCHEMA_VERSION = "hft3_meta_training_set_v1"


def _signal_and_features(model_id: str, npz_path: str, tick_size: float) -> pd.DataFrame:
    import numpy as np
    import pandas as pd
    from backtest_pipeline.src.hftbacktest_only_pipeline import _canonical_signal_adapter
    from features_engine.src.features.npz_feed import iter_mbo_events, load_npz_events
    from features_engine.src.pipeline.market_state_pipeline import MarketStatePipeline

    adapter_kind, adapter, _class_name = _canonical_signal_adapter(model_id)
    if adapter_kind != "hypothesis":
        raise ValueError(f"meta_training_unsupported_adapter:{adapter_kind}:{model_id}")
    raw_events = load_npz_events(npz_path)
    pipeline = MarketStatePipeline(tick_size=tick_size, latency_ms=0.1)
    rows: list[dict[str, Any]] = []
    feature_names: list[str] | None = None
    for event in iter_mbo_events(raw_events):
        state = pipeline.process_event(event)
        features = state.primary_features
        if feature_names is None:
            feature_names = sorted(str(k) for k in features)
        row = {name: float(features.get(name, np.nan)) for name in feature_names}
        row["timestamp_ns"] = int(event.timestamp_ns)
        row["primary_signal"] = float(adapter.evaluate(state))
        row["mid_price"] = float(features.get("mid_price", np.nan))
        rows.append(row)
    frame = pd.DataFrame(rows)
    frame.attrs["feature_names"] = feature_names or []
    frame.attrs["feature_backend"] = getattr(pipeline, "feature_backend", "unknown")
    return frame


def build_tables(
    surface_manifest: Path,
    out_dir: Path,
    *,
    pt_ticks: float,
    sl_ticks: float,
    max_holding_ms: int,
    max_tapes_per_model: int = 0,
) -> dict[str, Any]:
    import numpy as np
    import pandas as pd
    from backtest_pipeline.src.instrument_specs import resolve_instrument_spec
    from decision_engine.python.src.targets import build_triple_barrier_labels

    out_dir.mkdir(parents=True, exist_ok=True)
    seen: set[tuple[str, str]] = set()
    per_model_counts: dict[str, int] = {}
    per_model_frames: dict[str, list[pd.DataFrame]] = {}
    feature_names_by_model: dict[str, list[str]] = {}
    backend_by_model: dict[str, set[str]] = {}

    with surface_manifest.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("blocker_code") or row.get("admissibility_status") not in ("", "admissible"):
                continue
            model_id = str(row["canonical_model_id"])
            npz_path = str(row["source_npz"])
            key = (model_id, npz_path)
            if key in seen:
                continue
            if max_tapes_per_model and per_model_counts.get(model_id, 0) >= max_tapes_per_model:
                continue
            seen.add(key)
            per_model_counts[model_id] = per_model_counts.get(model_id, 0) + 1
            spec = resolve_instrument_spec(str(row["symbol"]))
            frame = _signal_and_features(model_id, npz_path, spec.tick_size)
            if frame.empty:
                continue
            mids = frame["mid_price"].to_numpy(dtype=np.float64)
            ts = frame["timestamp_ns"].to_numpy(dtype=np.int64)
            sides = np.sign(frame["primary_signal"].to_numpy(dtype=np.float64))
            sides[sides == 0] = 1.0
            labels = build_triple_barrier_labels(
                mids,
                ts,
                pt_ticks=pt_ticks,
                sl_ticks=sl_ticks,
                max_holding_ms=max_holding_ms,
                tick_size=spec.tick_size,
                sides=sides,
            )
            frame["y_tb_outcome"] = labels["y_tb_outcome"].to_numpy()
            signal = frame["primary_signal"].to_numpy(dtype=np.float64)
            outcome = frame["y_tb_outcome"].to_numpy(dtype=np.float64)
            meta = np.full(len(frame), np.nan)
            fired = signal != 0.0
            meta[fired & (outcome == 1.0)] = 1.0
            meta[fired & ((outcome == -1.0) | (outcome == 0.0))] = 0.0
            frame["y_meta"] = meta
            frame["event_id"] = str(row["event_id"])
            frame["symbol"] = str(row["symbol"])
            per_model_frames.setdefault(model_id, []).append(frame)
            feature_names_by_model.setdefault(model_id, list(frame.attrs["feature_names"]))
            backend_by_model.setdefault(model_id, set()).add(str(frame.attrs["feature_backend"]))

    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "surface_manifest": str(surface_manifest),
        "pt_ticks": pt_ticks,
        "sl_ticks": sl_ticks,
        "max_holding_ms": max_holding_ms,
        "models": {},
    }
    for model_id, frames in per_model_frames.items():
        table = pd.concat(frames, ignore_index=True)
        table_path = out_dir / f"meta_training_{model_id}.parquet"
        try:
            table.to_parquet(table_path)
        except Exception:
            table_path = out_dir / f"meta_training_{model_id}.pkl"
            table.to_pickle(table_path)
        labeled = table["y_meta"].notna()
        feature_names = feature_names_by_model[model_id]
        receipt["models"][model_id] = {
            "table": str(table_path),
            "table_sha256": hashlib.sha256(table_path.read_bytes()).hexdigest(),
            "rows": int(len(table)),
            "labeled_rows": int(labeled.sum()),
            "positive_rate": float(table.loc[labeled, "y_meta"].mean()) if labeled.any() else None,
            "tapes": per_model_counts.get(model_id, 0),
            "feature_names": feature_names,
            "feature_backends": sorted(backend_by_model.get(model_id, set())),
        }
    receipt_path = out_dir / "meta_training_receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=1, sort_keys=True), encoding="utf-8")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface-manifest", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--pt-ticks", type=float, default=8.0)
    parser.add_argument("--sl-ticks", type=float, default=8.0)
    parser.add_argument("--max-holding-ms", type=int, default=30_000)
    parser.add_argument("--max-tapes-per-model", type=int, default=0)
    args = parser.parse_args(argv)
    receipt = build_tables(
        args.surface_manifest,
        args.out_dir,
        pt_ticks=args.pt_ticks,
        sl_ticks=args.sl_ticks,
        max_holding_ms=args.max_holding_ms,
        max_tapes_per_model=args.max_tapes_per_model,
    )
    print(json.dumps({m: v["labeled_rows"] for m, v in receipt["models"].items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
