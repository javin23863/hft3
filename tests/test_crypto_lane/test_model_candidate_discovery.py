"""Model candidate discovery."""
from __future__ import annotations

from crypto_lane.src.ml.candidate_registry import discover_candidates, validate_candidate


def test_discovers_seven_candidates():
    cands = discover_candidates()
    assert len(cands) == 7
    ids = {c["candidate_id"] for c in cands}
    assert "crypto_h1_basis_compression" in ids
    assert "crypto_h7_congestion_event_study" in ids


def test_candidates_validate():
    for c in discover_candidates():
        errs = validate_candidate(c)
        assert not errs, f"{c['candidate_id']}: {errs}"
