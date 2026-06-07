"""fill-test-gaps orchestrator dry-run and fail-fast."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from crypto_lane.src.ingest.fill_test_gaps import run_fill_test_gaps


def test_fill_test_gaps_dry_run_single_readiness_call():
    audit = {
        "crypto_ready": False,
        "crypto_l3_ready": False,
        "crypto_mempool_ready": False,
        "purge_safe": False,
        "purge_safe_estimate": False,
        "days_until_purge_safe": 274,
        "preflight_l3": {"purge_safe": False, "synthetic_days": 274},
        "preflight_mempool": {"mempool_ready": False},
        "cae_bookticker_backfill_status": {"days_until_purge_safe": 274},
    }
    builder = MagicMock(return_value=audit)
    with patch(
        "crypto_lane.src.ingest.fill_test_gaps.clear_bookticker_summary_cache",
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps.ensure_crypto_env",
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps.redacted_env_report",
        return_value={"b2_configured": True},
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps.build_crypto_readiness_report",
        builder,
    ):
        report = run_fill_test_gaps(dry_run=True)
    assert report["dry_run"] is True
    assert builder.call_count == 1
    assert "preflight_l3" in report
    assert report["ready"] is False
    assert report["crypto_audit"]["crypto_ready"] is False
    assert report.get("pit_strict_blocked") is True


def test_fill_test_gaps_fail_fast_on_pull_gold():
    with patch(
        "crypto_lane.src.ingest.fill_test_gaps.clear_bookticker_summary_cache",
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps.ensure_crypto_env",
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps.redacted_env_report",
        return_value={},
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps.pull_gold",
        side_effect=RuntimeError("network down"),
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps.normalize_all",
    ) as mock_norm:
        report = run_fill_test_gaps(dry_run=False, continue_on_error=False)
    assert "pull_gold" in report["errors"][0]
    assert report["ready"] is False
    mock_norm.assert_not_called()


def test_fill_test_gaps_continue_on_error():
    with patch(
        "crypto_lane.src.ingest.fill_test_gaps.clear_bookticker_summary_cache",
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps.ensure_crypto_env",
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps.redacted_env_report",
        return_value={},
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps.pull_gold",
        side_effect=RuntimeError("network down"),
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps.preflight_mempool_gaps",
        return_value={"mempool_ready": True},
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps.preflight_l3_gaps",
        return_value={"purge_safe": False, "synthetic_days": 0},
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps._fill_l3_gaps",
        return_value={"aborted": False},
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps.normalize_all",
        return_value={"spot_perp_ticks.csv": "/tmp/x"},
    ), patch(
        "crypto_lane.src.ingest.fill_test_gaps.build_crypto_readiness_report",
        return_value={"crypto_ready": False},
    ):
        report = run_fill_test_gaps(dry_run=False, continue_on_error=True)
    assert report.get("normalize") is not None
