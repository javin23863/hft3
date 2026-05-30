"""Phase 7 — multi-asset L3 event replay wrapper (extends single-NPZ ReplayRunner)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from backtest_pipeline.src.runner import LATENCY_BANDS_MS, ReplayRunner
from hfc3.events.l3_event_snapshot_tensor import SNAPSHOT_OFFSETS_SEC, write_l3_event_tensor
from hfc3.features.cross_asset_l3_event_features import build_cross_asset_l3_features
from hfc3.labels.l3_event_targets import build_l3_event_targets

CHI404_LATENCY_BANDS_MS = [0.5, 1.0, 2.0]


def run_l3_cross_asset_event_replay(
    repo_root: Path,
    event_id: str,
    *,
    execution_symbol: str = "MES.v.0",
    feature_symbols: Optional[Sequence[str]] = None,
    latency_bands_ms: Optional[Sequence[float]] = None,
    queue_model: str = "LogProbQueueModel2",
) -> Dict[str, Any]:
    """
    1. Build multi-symbol MBO snapshot tensor (cross-asset state).
    2. Run HftBacktest replay on execution_symbol NPZ across latency bands.
    Cross-asset features inform analysis artifacts; execution remains single-instrument.
    """
    latency_bands = list(latency_bands_ms or LATENCY_BANDS_MS)
    pq, meta_path = write_l3_event_tensor(
        repo_root,
        event_id,
        symbols=feature_symbols,
        latency_band_ms=1.0,
    )
    tensor_df = __import__("pandas").read_parquet(pq)
    cross_feats = build_cross_asset_l3_features(tensor_df, offset_sec=0)
    targets = build_l3_event_targets(tensor_df)

    from backtest.adapters.rithmic_replay_loader import resolve_event_npz

    npz_path = resolve_event_npz(event_id, repo_root, symbol=execution_symbol)
    runner = ReplayRunner(str(npz_path), product=execution_symbol.split(".")[0])

    band_results: List[Dict[str, Any]] = []
    for band in latency_bands:
        result = runner.run_replay(latency_ms=float(band), queue_model=queue_model, use_combined_strategy=False)
        band_results.append({"latency_ms": band, "steps": result.get("steps"), "status": result.get("status")})

    out_dir = repo_root / "runtime" / "reports" / "l3_cross_asset_replay"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"{event_id}_replay.json"
    payload = {
        "event_id": event_id,
        "execution_symbol": execution_symbol,
        "tensor_parquet": str(pq.relative_to(repo_root)),
        "tensor_meta": str(meta_path.relative_to(repo_root)),
        "cross_asset_features_t0": cross_feats,
        "target_row_count": int(len(targets)),
        "latency_bands_ms": latency_bands,
        "chi404_bands_present": all(b in latency_bands for b in CHI404_LATENCY_BANDS_MS),
        "band_results": band_results,
        "filtration_note": "Targets and cross-asset features built from MBO snapshots; replay uses execution NPZ only",
    }
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
