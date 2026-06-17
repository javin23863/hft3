#!/usr/bin/env python
"""Official HftBacktest source-lock, data-validation, and latency-gate runner."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "packages")]

from backtest_pipeline.src.hftbacktest_realism import write_hftbacktest_realism_artifacts


def _default_run_id() -> str:
    return f"hbt_realism_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write HftBacktest source lock, data validation, latency gate, and fail-closed artifact",
    )
    parser.add_argument(
        "--screening-artifact",
        type=Path,
        required=True,
        help="Terminal VectorBT screening_artifact.json to hand off to HftBacktest",
    )
    parser.add_argument(
        "--data-npz",
        type=Path,
        default=None,
        help="HftBacktest NPZ input containing a structured event array under key 'data'",
    )
    parser.add_argument(
        "--latency-model",
        type=Path,
        default=None,
        help="Latency artifact JSON containing measured/proxy HftBacktest latency contract fields",
    )
    parser.add_argument(
        "--fill-queue-model",
        type=Path,
        default=None,
        help="Fill/queue artifact JSON containing HftBacktest exchange, queue, fee, tick, lot, and market-impact contract fields",
    )
    parser.add_argument(
        "--observation-artifact",
        type=Path,
        default=None,
        help="Optional offline paper/live observation JSON for HBT-5 replay-vs-observed discrepancy comparison",
    )
    parser.add_argument("--candidate-id", default=None, help="Promoted candidate id to select")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--repo-root", type=Path, default=REPO)
    parser.add_argument(
        "--out-root",
        type=Path,
        default=None,
        help="Output root; default is <repo>/research_cards/hftbacktest_realism",
    )
    parser.add_argument(
        "--hftbacktest-upstream-ref",
        default=None,
        help="Pinned upstream HftBacktest commit SHA or tag used for this run",
    )
    parser.add_argument(
        "--native-hot-path-evidence",
        action="append",
        default=[],
        help="Native C++ hot-path evidence artifact path; repeat for multiple artifacts",
    )
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    run_id = args.run_id or _default_run_id()
    out_root = args.out_root.resolve() if args.out_root else repo_root / "research_cards" / "hftbacktest_realism"
    payload = write_hftbacktest_realism_artifacts(
        repo_root=repo_root,
        out_dir=out_root / run_id,
        screening_artifact_path=args.screening_artifact.resolve(),
        data_npz_path=args.data_npz.resolve() if args.data_npz else None,
        latency_model_path=args.latency_model.resolve() if args.latency_model else None,
        fill_queue_model_path=args.fill_queue_model.resolve() if args.fill_queue_model else None,
        observation_artifact_path=args.observation_artifact.resolve() if args.observation_artifact else None,
        candidate_id=args.candidate_id,
        upstream_ref=args.hftbacktest_upstream_ref,
        native_hot_path_evidence=list(args.native_hot_path_evidence or []),
        run_id=run_id,
    )
    summary = payload["replay_summary"]
    result = {
        "run_id": run_id,
        "artifact_dir": str(out_root / run_id),
        "replay_realism_status": summary["replay_realism_status"],
        "fail_closed_reasons": summary["fail_closed_reasons"],
        "source_lock_path": payload["source_lock_path"],
        "latency_model_path": payload["latency_model_path"],
        "fill_queue_model_path": payload["fill_queue_model_path"],
        "data_validation_path": str(out_root / run_id / "data_validation.json"),
        "replay_summary_path": payload["replay_summary_path"],
    }
    print(json.dumps(result, indent=2))
    return 0 if summary["replay_realism_status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
