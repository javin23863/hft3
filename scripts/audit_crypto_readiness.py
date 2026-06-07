#!/usr/bin/env python3
"""Crypto-only readiness audit; exit 0 only when crypto_ready is true."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
from hft3_bootstrap import setup_repo_paths

setup_repo_paths()


def crypto_readiness_report() -> dict[str, Any]:
    from crypto_lane.src.ingest.bookticker_quality import clear_bookticker_summary_cache
    from crypto_lane.src.ingest.cae_backfill_status import cae_bookticker_backfill_status
    from crypto_lane.src.ingest.l3_preflight import preflight_l3_gaps
    from scripts.audit_all_research_data import _crypto_gaps

    clear_bookticker_summary_cache()
    crypto = _crypto_gaps()
    dr = crypto.get("crypto_date_range") or {}
    start = str(dr.get("start", "2024-01-01"))
    end = str(dr.get("end", "2024-12-31"))
    l3_pf = preflight_l3_gaps(start=start, end=end, vision_probe=False)
    cae = cae_bookticker_backfill_status(start=start, end=end, l3_preflight=l3_pf)
    return {
        **crypto,
        "purge_safe": bool(l3_pf.get("purge_safe")),
        "purge_block_reason": l3_pf.get("purge_block_reason"),
        "l3_recommendation": l3_pf.get("recommendation"),
        "cae_bookticker_backfill_status": cae,
        "days_until_purge_safe": cae.get("days_until_purge_safe"),
    }


def main() -> int:
    report = crypto_readiness_report()
    out = _REPO / "runtime/data_audits/crypto_readiness.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report.get("crypto_ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
