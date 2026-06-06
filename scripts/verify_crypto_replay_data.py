#!/usr/bin/env python3
"""Verify real crypto replay NPZ setup and honest routing labels."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "packages"))

from backtest_pipeline.src.asset_class_routing import (
    EXEC_CLASS_L2_DEPTH_VALIDATED,
    ExecutionCapability,
    resolve_validation_path,
)
from research_pipeline.types import CandidateModel

KRAKEN_NPZ = [
    ("BTC/USD", REPO / "data/replay/hftbacktest/crypto/kraken/BTC_USD"),
    ("ETH/USD", REPO / "data/replay/hftbacktest/crypto/kraken/ETH_USD"),
    ("SOL/USD", REPO / "data/replay/hftbacktest/crypto/kraken/SOL_USD"),
]

BINANCE_NPZ = [
    ("BTCUSDT", REPO / "data/replay/hftbacktest/crypto/binance/btcusdt"),
    ("ETHUSDT", REPO / "data/replay/hftbacktest/crypto/binance/ethusdt"),
    ("SOLUSDT", REPO / "data/replay/hftbacktest/crypto/binance/solusdt"),
]

PERP_TO_MODEL = {
    "BTCUSDT": "CRYPTO_H1",
    "ETHUSDT": "CRYPTO_H2",
    "SOLUSDT": "CRYPTO_H3",
}


def _npz_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.npz")) if directory.is_dir() else []


def _check_kraken_meta(npz: Path) -> list[str]:
    errors: list[str] = []
    meta_path = npz.with_name(npz.stem + ".meta.json")
    if not meta_path.is_file():
        errors.append(f"missing meta sidecar: {meta_path.relative_to(REPO)}")
        return errors
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("data_class") != "L2_DEPTH":
        errors.append(f"{meta_path.name}: expected data_class=L2_DEPTH, got {meta.get('data_class')!r}")
    if meta.get("execution_classification") != EXEC_CLASS_L2_DEPTH_VALIDATED:
        errors.append(
            f"{meta_path.name}: expected execution_classification={EXEC_CLASS_L2_DEPTH_VALIDATED!r}, "
            f"got {meta.get('execution_classification')!r}"
        )
    return errors


def main() -> int:
    errors: list[str] = []

    print("=== Kraken WS book-depth NPZ ===")
    for symbol, directory in KRAKEN_NPZ:
        files = _npz_files(directory)
        if not files:
            errors.append(f"no NPZ in {directory.relative_to(REPO)} — run: python scripts/setup_crypto_replay_data.py")
            continue
        npz = files[0]
        events = len(np.load(npz)["data"])
        print(f"  OK  {symbol}: {events} events -> {npz.relative_to(REPO)}")
        errors.extend(_check_kraken_meta(npz))

    print("\n=== Binance L2 depth NPZ ===")
    for symbol, directory in BINANCE_NPZ:
        files = _npz_files(directory)
        if not files:
            errors.append(f"no NPZ in {directory.relative_to(REPO)}")
            continue
        npz = files[0]
        events = len(np.load(npz)["data"])
        print(f"  OK  {symbol}: {events} events -> {npz.relative_to(REPO)}")

    print("\n=== Routing (Kraken depth -> L2_DEPTH_VALIDATION, not L3) ===")
    for perp, model in PERP_TO_MODEL.items():
        candidate = CandidateModel(
            candidate_id=f"{model}_verify",
            model_id=model,
            strategy_params={},
            thesis="verify routing",
            metadata={"symbol": perp},
        )
        path = resolve_validation_path(candidate, REPO)
        if path.execution_capability != ExecutionCapability.L2_DEPTH_VALIDATION:
            errors.append(f"{perp}: expected L2_DEPTH_VALIDATION, got {path.execution_capability.name}")
        else:
            print(f"  OK  {model} {perp} -> {path.execution_capability.name} ({EXEC_CLASS_L2_DEPTH_VALIDATED})")

    if errors:
        print("\nFAIL:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("\nPASS: real replay NPZ present, Kraken meta honest (L2_DEPTH), routing correct.")
    print("Replay integration test (not a backtest): pytest tests/test_crypto_l2/test_crypto_execution_validator.py::test_run_crypto_replay_with_kraken_depth -q")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
