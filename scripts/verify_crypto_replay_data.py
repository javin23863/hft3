#!/usr/bin/env python3
"""Verify L3 MBO replay NPZ and routing (L3 only — not L2 depth)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "packages"))

from backtest_pipeline.src.asset_class_routing import (
    EXEC_CLASS_L3_VALIDATED,
    ExecutionCapability,
    resolve_validation_path,
)
from research_pipeline.types import CandidateModel

BITFINEX_MBO_NPZ = [
    ("BTC_USD", REPO / "data/replay/hftbacktest/crypto/bitfinex/BTC_USD"),
    ("ETH_USD", REPO / "data/replay/hftbacktest/crypto/bitfinex/ETH_USD"),
    ("SOL_USD", REPO / "data/replay/hftbacktest/crypto/bitfinex/SOL_USD"),
]

PERP_TO_MODEL = {
    "BTCUSDT": "CRYPTO_H1",
    "ETHUSDT": "CRYPTO_H2",
    "SOLUSDT": "CRYPTO_H3",
}


def _npz_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.npz")) if directory.is_dir() else []


def _check_meta(npz: Path) -> list[str]:
    errors: list[str] = []
    meta_path = npz.with_name(npz.stem + ".meta.json")
    if not meta_path.is_file():
        errors.append(f"missing meta sidecar: {meta_path.relative_to(REPO)}")
        return errors
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("data_class") != "L3_MBO":
        errors.append(f"{meta_path.name}: expected data_class=L3_MBO, got {meta.get('data_class')!r}")
    if meta.get("execution_classification") != EXEC_CLASS_L3_VALIDATED:
        errors.append(
            f"{meta_path.name}: expected execution_classification={EXEC_CLASS_L3_VALIDATED!r}, "
            f"got {meta.get('execution_classification')!r}"
        )
    return errors


def main() -> int:
    errors: list[str] = []
    mbo_count = 0

    print("=== Bitfinex R0 MBO NPZ (L3 only) ===")
    for symbol, directory in BITFINEX_MBO_NPZ:
        files = _npz_files(directory)
        if not files:
            errors.append(f"no L3 MBO NPZ in {directory.relative_to(REPO)} — run: python scripts/download_crypto_mbo.py")
            print(f"  FAIL {symbol}: missing")
            continue
        mbo_count += 1
        npz = files[0]
        events = len(np.load(npz)["data"])
        print(f"  OK  {symbol}: {events} events -> {npz.relative_to(REPO)}")
        errors.extend(_check_meta(npz))

    print("\n=== Routing (expect L3_VALIDATED when MBO present) ===")
    for perp, model in PERP_TO_MODEL.items():
        candidate = CandidateModel(
            candidate_id=f"{model}_verify",
            model_id=model,
            strategy_params={},
            thesis="verify routing",
            metadata={"symbol": perp},
        )
        path = resolve_validation_path(candidate, REPO)
        if mbo_count > 0:
            if path.execution_capability != ExecutionCapability.L3_VALIDATED:
                errors.append(f"{perp}: expected L3_VALIDATED, got {path.execution_capability.name}")
            else:
                print(f"  OK  {model} {perp} -> {path.execution_capability.name} ({EXEC_CLASS_L3_VALIDATED})")
        else:
            print(f"  --  {model} {perp} -> {path.execution_capability.name} (no MBO yet)")

    if errors:
        print("\nFAIL:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"\nPASS: {mbo_count} L3 MBO symbol(s) ready; routing -> L3_VALIDATED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
