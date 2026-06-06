#!/usr/bin/env python3
"""Convert committed crypto raw NDJSON → replay NPZ (real recordings only).

Skips empty files and live REST fetches. Does not run backtests.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "packages" / "crypto_lane" / "pipeline.py"
MIN_BYTES = 64

KRAKEN_JOBS = [
    ("data/crypto/kraken_l3_raw/kraken_l3_BTC_USD_20260601_073229.ndjson", "BTC/USD"),
    ("data/crypto/kraken_l3_raw/kraken_l3_ETH_USD_20260601_073229.ndjson", "ETH/USD"),
    ("data/crypto/kraken_l3_raw/kraken_l3_SOL_USD_20260601_073809.ndjson", "SOL/USD"),
]

BINANCE_JOBS = [
    ("data/crypto/binance_l2_raw/binance_l2_btcusdt_20260601_074847.ndjson", "BTCUSDT"),
    ("data/crypto/binance_l2_raw/binance_l2_ethusdt_20260601_074847.ndjson", "ETHUSDT"),
    ("data/crypto/binance_l2_raw/binance_l2_solusdt_20260601_074847.ndjson", "SOLUSDT"),
]


def _run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=REPO, check=True)


def _convert_kraken(ndjson: Path, symbol: str) -> None:
    _run([sys.executable, str(PIPELINE), "convert-l3", str(ndjson), "--routing-symbol", symbol])


def _convert_binance(ndjson: Path, symbol: str) -> None:
    _run(
        [
            sys.executable,
            str(PIPELINE),
            "convert-l2",
            str(ndjson),
            "--routing-symbol",
            symbol,
            "--no-fetch",
        ]
    )


def main() -> int:
    if not PIPELINE.is_file():
        print(f"ERROR: pipeline not found: {PIPELINE}", file=sys.stderr)
        return 1

    for rel, symbol in KRAKEN_JOBS + BINANCE_JOBS:
        path = REPO / rel
        if not path.is_file():
            print(f"SKIP missing: {rel}", flush=True)
            continue
        if path.stat().st_size < MIN_BYTES:
            print(f"SKIP empty/tiny ({path.stat().st_size} B): {rel}", flush=True)
            continue
        if symbol.endswith("USDT"):
            _convert_binance(path, symbol)
        else:
            _convert_kraken(path, symbol)

    verify = REPO / "scripts" / "verify_crypto_replay_data.py"
    if verify.is_file():
        _run([sys.executable, str(verify)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
