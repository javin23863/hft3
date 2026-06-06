#!/usr/bin/env python3
"""Record Bitfinex R0 order-level MBO and convert to replay NPZ (L3 pipeline).

Source: wss://api.bitfinex.com/ws/2  prec=R0  (true MBO, public, no API key).
Re-run to append raw sessions; use --merge-all on convert to build cumulative NPZ.

Pipeline pass criteria (L3_VALIDATED execution replay gate):
  - NPZ at data/replay/hftbacktest/crypto/bitfinex/{BTC_USD,ETH_USD,SOL_USD}/*_mbo.npz
  - Sidecar meta: data_class=L3_MBO, execution_classification=L3_VALIDATED
  - Routing resolves L3_VALIDATED for BTCUSDT/ETHUSDT/SOLUSDT candidates
  - validate_crypto_candidate replay completes with L3FifoQueueModel
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "packages" / "crypto_lane" / "pipeline.py"
RAW_DIR = REPO / "data" / "crypto" / "bitfinex_mbo_raw"

SYMBOLS = ["tBTCUSD", "tETHUSD", "tSOLUSD"]
SYMBOL_TO_ROUTE = {"tBTCUSD": "BTC_USD", "tETHUSD": "ETH_USD", "tSOLUSD": "SOL_USD"}

# Matches crypto candidate fixture_observation_seconds (production validation tier)
DEFAULT_RECORD_SECONDS = 512.0


def _run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=REPO, check=True)


def _normalize_symbol(sym: str) -> str:
    s = sym.strip()
    if not s.startswith("t"):
        s = f"t{s.upper()}"
    return s


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Download crypto L3 MBO via Bitfinex R0 (public)")
    parser.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_RECORD_SECONDS,
        help=f"Recording seconds per run (default {DEFAULT_RECORD_SECONDS:.0f}, matches fixture_observation_seconds)",
    )
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    parser.add_argument("--record-only", action="store_true", help="Record raw NDJSON only, skip convert")
    parser.add_argument("--convert-only", action="store_true", help="Merge+convert existing raw, skip record")
    parser.add_argument(
        "--no-merge-all",
        action="store_true",
        help="Convert latest session only instead of merging all raw files per symbol",
    )
    args = parser.parse_args()

    symbols = [_normalize_symbol(s) for s in args.symbols.split(",") if s.strip()]

    if not args.convert_only:
        _run(
            [
                sys.executable,
                str(PIPELINE),
                "record-mbo",
                "--symbols",
                ",".join(symbols),
                "--duration",
                str(args.duration),
            ]
        )

    if args.record_only:
        return 0

    converted = 0
    for symbol in symbols:
        route = SYMBOL_TO_ROUTE.get(symbol, symbol)
        if args.no_merge_all:
            safe = symbol.lstrip("t")
            matches = sorted(RAW_DIR.glob(f"bitfinex_mbo_{safe}_*.ndjson"), key=lambda p: p.stat().st_mtime)
            if not matches or matches[-1].stat().st_size < 64:
                print(f"SKIP no L3 data for {symbol}", flush=True)
                continue
            cmd = [
                sys.executable,
                str(PIPELINE),
                "convert-mbo",
                str(matches[-1]),
                "--routing-symbol",
                route,
            ]
        else:
            cmd = [
                sys.executable,
                str(PIPELINE),
                "convert-mbo",
                "--merge-all",
                "--raw-dir",
                str(RAW_DIR),
                "--bitfinex-symbol",
                symbol,
                "--routing-symbol",
                route,
            ]
        _run(cmd)
        converted += 1

    if converted == 0:
        print("FAIL: no L3 MBO to convert", flush=True)
        return 1

    verify = REPO / "scripts" / "verify_crypto_replay_data.py"
    if verify.is_file():
        _run([sys.executable, str(verify)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
