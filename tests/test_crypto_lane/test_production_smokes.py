"""Production crypto smokes — gated on crypto_ready (cached audit file)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_READINESS_CACHE = _REPO / "runtime/data_audits/crypto_readiness.json"


def _crypto_ready() -> bool:
    if _READINESS_CACHE.is_file():
        try:
            return bool(json.loads(_READINESS_CACHE.read_text(encoding="utf-8")).get("crypto_ready"))
        except (json.JSONDecodeError, OSError):
            pass
    return False


pytestmark = pytest.mark.production


@pytest.mark.skipif(not _crypto_ready(), reason="crypto_ready false; run audit_crypto_readiness.py first")
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
