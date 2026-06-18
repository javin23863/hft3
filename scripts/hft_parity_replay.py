#!/usr/bin/env python3
"""Old-vs-new HftBacktest parity replay checks."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from hft3_bootstrap import repo_root

from backtest_pipeline.src.hft_campaign.prepared_data import prepare_replay_data
from backtest_pipeline.src.replay_matrix import run_hypothesis_replay
from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz
from features_engine.src.hypotheses.registry import get_active_hypotheses


def main() -> int:
    parser = argparse.ArgumentParser(description="HftBacktest parity replay")
    parser.add_argument("--corpus", default="")
    args = parser.parse_args()

    root = repo_root()
    with tempfile.TemporaryDirectory() as tmp:
        npz_path = Path(tmp) / "minimal.npz"
        build_minimal_mbo_npz(npz_path)
        prepared = prepare_replay_data(
            source_npz_path=npz_path,
            repo_root=root,
            symbol="MES",
            event_id="PARITY_SMOKE",
        )
        hyp = get_active_hypotheses()[0]
        legacy = run_hypothesis_replay(hyp, str(npz_path), max_steps=500)
        new = run_hypothesis_replay(hyp, str(prepared.path), max_steps=500)
        report = {
            "legacy_num_trades": legacy.num_trades,
            "new_num_trades": new.num_trades,
            "legacy_net_pnl": legacy.net_pnl,
            "new_net_pnl": new.net_pnl,
            "prepared_data_hash": prepared.prepared_data_hash,
        }
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
