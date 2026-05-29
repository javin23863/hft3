"""
Master offline orchestrator (pre-bare-metal): probe download -> convert -> research matrix.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--npz", default=None, help="Path to existing NPZ for replay")
    parser.add_argument(
        "--rithmic-trial",
        action="store_true",
        help="Run quarantined Rithmic trial fixture capture->process (no production lake)",
    )
    args = parser.parse_args()

    if args.rithmic_trial:
        print("=== Rithmic trial lane (fixture) ===")
        cfg = _REPO / "data_system" / "config" / "rithmic_trial.yaml"
        env = {**os.environ, "RITHMIC_TRIAL_ENABLED": "1", "RITHMIC_TRIAL_CONNECTOR": "fixture"}

        r1 = subprocess.run(
            [
                sys.executable,
                "-m",
                "data_system.rithmic_trial.pipeline",
                "capture",
                "--config",
                str(cfg),
                "--force",
                "--duration-sec",
                "1",
            ],
            cwd=str(_REPO),
            env=env,
        )
        if r1.returncode != 0:
            print("Rithmic trial capture failed")
            return
        from datetime import datetime, timezone

        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        r2 = subprocess.run(
            [
                sys.executable,
                "-m",
                "data_system.rithmic_trial.pipeline",
                "process",
                "--config",
                str(cfg),
                "--date",
                date,
                "--symbol",
                "MES",
            ],
            cwd=str(_REPO),
        )
        if r2.returncode != 0:
            print("Rithmic trial process failed")
            return
        npz = _REPO / "data" / "replay" / "hftbacktest" / "rithmic_trial" / date / "MES" / f"MES_{date}_trial.npz"
        if npz.exists():
            args.npz = str(npz)
            print(f"Trial replay NPZ: {npz}")

    if not args.skip_download:
        print("=== Phase 3: Databento micro-probe ===")
        r = subprocess.run(
            [sys.executable, str(_REPO / "data_system" / "scripts" / "download_micro_probe.py")],
            cwd=str(_REPO),
        )
        if r.returncode != 0:
            print("Download skipped or failed; continue with --npz if you have data.")

    npz_path = args.npz
    if not npz_path:
        data_dir = _REPO / "data"
        candidates = list((_REPO / "data" / "npz").glob("*.npz")) if (_REPO / "data" / "npz").exists() else []
        if not candidates and data_dir.exists():
            candidates = list(data_dir.glob("**/*.npz"))
        if candidates:
            npz_path = str(candidates[0])
            print(f"Using NPZ: {npz_path}")
        else:
            dbn = list(data_dir.glob("**/*.dbn.zst")) if data_dir.exists() else []
            if dbn:
                print("=== Converting DBN -> NPZ ===")
                subprocess.run(
                    [
                        sys.executable,
                        str(_REPO / "backtest_pipeline" / "src" / "converter.py"),
                        "--input",
                        str(dbn[0]),
                        "--symbol",
                        "MES.v.0",
                    ],
                    cwd=str(_REPO),
                    check=False,
                )
                candidates = list((_REPO / "data" / "npz").glob("*.npz"))
                if candidates:
                    npz_path = str(candidates[0])

    print("=== Phase 6-7: Research matrix smoke ===")
    from backtest_pipeline.src.research_runner import run_hypothesis_matrix

    run_hypothesis_matrix(npz_path)

    card_path = _REPO / "research_cards" / "matrix_smoke.json"
    if card_path.exists():
        print(json.dumps(json.loads(card_path.read_text()), indent=2)[:2000])


if __name__ == "__main__":
    main()
