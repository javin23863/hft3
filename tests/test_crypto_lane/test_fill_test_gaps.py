"""fill-test-gaps orchestrator dry-run."""
from __future__ import annotations

from unittest.mock import patch

from crypto_lane.src.ingest.fill_test_gaps import run_fill_test_gaps


def test_fill_test_gaps_dry_run():
    audit = {
        "crypto_ready": False,
        "crypto_l3_ready": False,
        "crypto_mempool_ready": False,
        "purge_safe": False,
        "days_until_purge_safe": 274,
    }
    with patch(
        "crypto_lane.src.ingest.fill_test_gaps.clear_bookticker_summary_cache",
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps.ensure_crypto_env",
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps.redacted_env_report",
        return_value={"b2_configured": True},
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps.preflight_l3_gaps",
        return_value={"purge_safe": False, "synthetic_days": 274},
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps.preflight_mempool_gaps",
        return_value={"mempool_ready": False},
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps.cae_bookticker_backfill_status",
        return_value={"days_until_purge_safe": 274},
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps._crypto_audit_snapshot",
        return_value=audit,
    ):
        report = run_fill_test_gaps(dry_run=True)
    assert report["dry_run"] is True
    assert "preflight_l3" in report
    assert report["ready"] is False
    assert report["crypto_audit"]["crypto_ready"] is False
