"""Production crypto smokes — gated on fresh crypto_ready cache."""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_READINESS_CACHE = _REPO / "runtime/data_audits/crypto_readiness.json"


def _crypto_ready() -> bool:
    from crypto_lane.src.ingest.bookticker_quality import summarize_bookticker_range
    from crypto_lane.src.ingest.crypto_readiness import readiness_cache_fresh

    if not _READINESS_CACHE.is_file():
        return False
    try:
        cached = json.loads(_READINESS_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    dr = cached.get("crypto_date_range") or {}
    start = str(dr.get("start", "2024-01-01"))
    end = str(dr.get("end", "2024-12-31"))
    try:
        live_syn = len(summarize_bookticker_range(start=start, end=end, use_cache=True)["synthetic"])
    except OSError:
        return False
    return readiness_cache_fresh(cached, live_synthetic_days=live_syn)


pytestmark = pytest.mark.production


@pytest.mark.skipif(
    not _crypto_ready(),
    reason="crypto_ready false or stale cache; run audit_crypto_readiness.py first",
)
def test_all_production_candidate_smokes_complete():
    from crypto_lane.src.ml.walk_forward_runner import run_all_smokes

    reports = run_all_smokes(production=True)
    assert len(reports) == 7
    for report in reports:
        assert report["candidate_id"]
        assert report["target"]
        assert report.get("smoke_mode") is False
        assert "with_btc_node" in report["runs"] or "without_btc_node" in report["runs"]
        primary = report["runs"].get("with_btc_node") or report["runs"].get("without_btc_node")
        assert primary is not None
        min_folds = 2 if report.get("hypothesis_id") == "CRYPTO_H7" else 3
        assert primary.get("n_folds", 0) >= min_folds
        assert primary.get("min_folds_met") is True
        assert report.get("purged_cv_implemented") is True
        assert report["pass_fail"] == "pass"
        assert report.get("rejection_reason") is None
        assert report["holdout_gate"]["status"] == "PASS"
        nc = report["negative_controls"]
        assert nc.get("controls_skipped_low_signal") is False
        assert nc.get("shuffled_degraded") is True
        assert nc.get("shifted_degraded") is True
        if report.get("hypothesis_id") == "CRYPTO_H7":
            assert nc.get("randomized_degraded") is True
