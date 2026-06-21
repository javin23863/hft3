"""Phase 7 — three-generation acceptance (assignment §21).

VALIDATION_HONESTY: fixture_dry_run only — planted runners and minimal NPZ fixtures.
Not scope-green merge evidence for live manifests or production VectorBT/HftBacktest runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.run_autoresearch_three_gen_acceptance import (
    PLANTED_EXPECTED_OUTCOMES,
    run_three_gen_acceptance,
)


@pytest.mark.fixture_dry_run
def test_three_gen_acceptance_fixture_dry_run(tmp_path: Path) -> None:
    report_path = tmp_path / "acceptance_report.md"
    payload = run_three_gen_acceptance(repo_root=tmp_path, report_path=report_path)
    assert payload["exit_code"] == 0
    assert payload["mode"] == "fixture_dry_run"
    assert payload["generations_run"] == 3
    assert report_path.is_file()
    text = report_path.read_text(encoding="utf-8")
    assert "Generation 0" in text
    assert "Generation 1" in text
    assert "Generation 2" in text
    assert "Stop reason" in text or "stop reason" in text.lower()
    assert payload["deduplication_tested_hash_count"] > 0
    assert payload["recipe_dimension_change_gen2"] is True

    gens = payload["generations"]
    assert gens[0]["proposed_count"] >= 1
    total_rejects = sum(sum(g["reject_counts"].values()) for g in gens)
    assert total_rejects >= 1 or gens[0]["final_pass_count"] >= 1
    assert payload["planted_outcomes"] == PLANTED_EXPECTED_OUTCOMES
    assert all(status != "FINAL_PASS" for status in payload["planted_outcomes"].values())
    for cid, expected in PLANTED_EXPECTED_OUTCOMES.items():
        assert f"`{cid}`: expected `{expected}`, actual `{expected}`" in text

    gen2_changes = payload["recipe_changes"].get(2) or []
    assert any(c.get("recipe_dimension_changed") for c in gen2_changes)
