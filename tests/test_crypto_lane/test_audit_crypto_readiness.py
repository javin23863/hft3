"""Crypto-only readiness audit script."""
from __future__ import annotations

from unittest.mock import patch


def test_crypto_readiness_report_merges_cae_status():
    from scripts.audit_crypto_readiness import crypto_readiness_report

    with patch(
        "crypto_lane.src.ingest.bookticker_quality.clear_bookticker_summary_cache",
    ), patch(
        "scripts.audit_all_research_data._crypto_gaps",
        return_value={
            "crypto_ready": False,
            "crypto_l3_ready": False,
            "crypto_mempool_ready": False,
            "crypto_date_range": {"start": "2024-01-01", "end": "2024-12-31"},
        },
    ), patch(
        "crypto_lane.src.ingest.l3_preflight.preflight_l3_gaps",
        return_value={
            "purge_safe": False,
            "purge_block_reason": "blocked",
            "recommendation": "do_not_replace_synthetic_until_b2_ready",
            "synthetic_day_list": ["2024-04-02"],
        },
    ), patch(
        "crypto_lane.src.ingest.cae_backfill_status.cae_bookticker_backfill_status",
        return_value={"days_until_purge_safe": 10},
    ):
        report = crypto_readiness_report()
    assert report["purge_safe"] is False
    assert report["days_until_purge_safe"] == 10
    assert "cae_bookticker_backfill_status" in report
