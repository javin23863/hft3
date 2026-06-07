#!/usr/bin/env python3
"""Crypto-only readiness audit; exit 0 only when crypto_ready is true."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
from hft3_bootstrap import setup_repo_paths

setup_repo_paths()


def crypto_readiness_report(*, refresh_b2_synthetic_probe: bool = True) -> dict:
    from crypto_lane.src.ingest.crypto_readiness import build_crypto_readiness_report

    return build_crypto_readiness_report(
        clear_cache=True,
        use_b2_synthetic_cache=not refresh_b2_synthetic_probe,
        refresh_b2_synthetic_probe=refresh_b2_synthetic_probe,
    )


def main(argv: list[str] | None = None) -> int:
    from crypto_lane.src.ingest.crypto_readiness import write_crypto_readiness_cache

    parser = argparse.ArgumentParser(description="Crypto readiness audit (writes crypto_readiness.json)")
    parser.add_argument(
        "--use-b2-cache",
        action="store_true",
        help="Reuse cached B2 synthetic probe (default: always refresh for gate file)",
    )
    args = parser.parse_args(argv)
    report = crypto_readiness_report(refresh_b2_synthetic_probe=not args.use_b2_cache)
    out = write_crypto_readiness_cache(report)
    report["readiness_gate_path"] = str(out)
    if report.get("b2_probe_note"):
        print(f"NOTE: {report['b2_probe_note']}", file=sys.stderr)
    print(json.dumps(report, indent=2))
    return 0 if report.get("crypto_ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
