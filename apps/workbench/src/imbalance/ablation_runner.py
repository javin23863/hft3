"""Imbalance ablation summaries (replay-backed only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from backtest.adapters.rithmic_replay_loader import RithmicReplayLoader, resolve_event_npz
from features_engine.src.imbalance.ablation import AblationRunResult
from workbench.src.imbalance.replay_runner import run_imbalance_ablation_replays
from workbench.src.run.run_context import RunContext


def summarize_ablation(results: List[AblationRunResult]) -> Dict[str, Any]:
    return {
        "results": [r.to_dict() for r in results],
        "promoted_modes": [r.mode_id for r in results if r.decision == "promote"],
        "rejected_modes": [r.mode_id for r in results if r.decision == "reject"],
    }


def run_imbalance_ablation_matrix(
    repo_root: Path,
    model_id: str,
    event_id: str,
    *,
    npz_path: Path | None = None,
    symbol: str | None = None,
    seed: int = 42,
    ablation_full: bool = False,
    fast_sweep: bool = True,
) -> tuple[List[AblationRunResult], Dict[str, Any]]:
    """Run replay-backed ablation; never accepts caller-supplied PnL stubs."""
    resolved = npz_path or resolve_event_npz(event_id, repo_root, symbol=symbol)
    if not Path(resolved).is_file():
        raise FileNotFoundError(f"NPZ missing for ablation: {resolved}")
    loader = RithmicReplayLoader()
    events = loader.load(str(resolved))
    ctx = RunContext.build(
        repo_root,
        model_id,
        event_id,
        Path(resolved),
        np.asarray(events),
        seed=seed,
    )
    ctx.metadata["npz_path"] = str(resolved)
    results, _samples, meta = run_imbalance_ablation_replays(
        ctx,
        fast_sweep=fast_sweep,
        ablation_full=ablation_full,
    )
    summary = summarize_ablation(results)
    summary["modes_run"] = meta.get("modes_run", [])
    summary["verdict"] = meta.get("verdict")
    summary["best_mode_id"] = meta.get("best_mode_id")
    summary["treatment_by_mode"] = meta.get("treatment_by_mode", {})
    return results, summary
