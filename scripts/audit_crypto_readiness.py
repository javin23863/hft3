#!/usr/bin/env python3
"""Crypto-only readiness audit; exit 0 only when crypto_ready is true."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
from hft3_bootstrap import setup_repo_paths

setup_repo_paths()


def crypto_readiness_report() -> dict:
    from crypto_lane.src.ingest.crypto_readiness import build_crypto_readiness_report

    return build_crypto_readiness_report(clear_cache=True, use_b2_synthetic_cache=True)


def main() -> int:
    from crypto_lane.src.ingest.crypto_readiness import write_crypto_readiness_cache

    report = crypto_readiness_report()
    out = write_crypto_readiness_cache(report)
    print(json.dumps(report, indent=2))
    return 0 if report.get("crypto_ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
