"""Crypto-only readiness audit script."""
from __future__ import annotations

from unittest.mock import patch


def test_crypto_readiness_report_delegates_to_builder():
    from scripts.audit_crypto_readiness import crypto_readiness_report

    expected = {
        "crypto_ready": False,
        "crypto_l3_ready": False,
        "crypto_mempool_ready": False,
        "purge_safe": False,
        "purge_safe_estimate": False,
        "days_until_purge_safe": 10,
        "cae_bookticker_backfill_status": {"days_until_purge_safe": 10},
        "audited_at": "2026-06-07T12:00:00+00:00",
    }
    with patch(
        "crypto_lane.src.ingest.crypto_readiness.build_crypto_readiness_report",
        return_value=expected,
    ) as mock_build:
        report = crypto_readiness_report()
    mock_build.assert_called_once_with(
        clear_cache=True,
        use_b2_synthetic_cache=False,
        refresh_b2_synthetic_probe=True,
    )
    assert report["purge_safe"] is False
    assert report["days_until_purge_safe"] == 10
    assert "cae_bookticker_backfill_status" in report


def test_audit_main_refreshes_b2_by_default():
    from scripts import audit_crypto_readiness

    with patch(
        "crypto_lane.src.ingest.crypto_readiness.build_crypto_readiness_report",
        return_value={"crypto_ready": False, "preflight_l3": {}},
    ) as mock_build, patch(
        "crypto_lane.src.ingest.crypto_readiness.write_crypto_readiness_cache",
        return_value=__import__("pathlib").Path("/tmp/x.json"),
    ):
        code = audit_crypto_readiness.main([])
    assert code == 1
    mock_build.assert_called_once_with(
        clear_cache=True,
        use_b2_synthetic_cache=False,
        refresh_b2_synthetic_probe=True,
    )
