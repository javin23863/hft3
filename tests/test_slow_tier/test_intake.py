"""Unit tests for src/intake.py — offline, no network, no ollama.

Covers:
- Disabled gate: exit 3 when intake.enabled=False
- Quota enforcement: exit 4 when >= weekly_quota candidates in trailing 7 days
- Quota does NOT fire when count is below quota
- Taxonomy load: all 6 families present with required fields
- Candidate JSON parsing: success and failure paths
- Candidate maps correctly to TestableHypothesis fields
- Quarantine flags are recorded (from detect_intake_quarantine)
- write_candidate() creates the JSON file and appends log record
- hypothesis_id format: SLOWTIER-{date}-{seq}
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parents[2]
_APPS = _REPO / "apps"
_PKGS = _REPO / "packages"
for p in (_APPS, _PKGS, str(_REPO)):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from llm_slow_tier.src.config import IntakeConfig, SlowTierConfig
from llm_slow_tier.src.intake import (
    load_taxonomy,
    count_recent_candidates,
    parse_candidate_json,
    build_and_validate_hypothesis,
    build_intake_prompt,
    write_candidate,
    run_intake,
    _next_hypothesis_seq,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(
    tmp_path: Path,
    enabled: bool = False,
    weekly_quota: int = 5,
) -> SlowTierConfig:
    cfg = SlowTierConfig()
    object.__setattr__(cfg, "artifact_root", str(tmp_path / "artifacts"))
    intake = IntakeConfig(enabled=enabled, weekly_quota=weekly_quota)
    object.__setattr__(cfg, "intake", intake)
    return cfg


def _make_log_record(days_ago: float = 0.0, hypothesis_id: str = "SLOWTIER-test-001") -> str:
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return json.dumps({
        "hypothesis_id": hypothesis_id,
        "family": "queue_position_mm",
        "date": ts.strftime("%Y-%m-%d"),
        "quarantine_flags": [],
        "model": "gemma4-test",
        "generated_utc": ts.isoformat(),
    })


def _make_log_with_n_recent(log_path: Path, n: int, days_ago: float = 0.0):
    records = [
        _make_log_record(days_ago=days_ago, hypothesis_id=f"SLOWTIER-test-{i:03d}")
        for i in range(1, n + 1)
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(records) + "\n", encoding="utf-8")


def _valid_candidate() -> Dict[str, Any]:
    return {
        "statement": "Queue position below 0.25 predicts fill probability > 80% within 10s.",
        "metric": "fill_probability",
        "threshold": 0.80,
        "pass_criteria": "Fill probability exceeds 0.80 in backtest with p < 0.05.",
        "fail_criteria": "Fill probability is at or below 0.80 or p >= 0.05.",
        "products": ["MES", "NQ"],
        "rationale": "Queue priority is a known edge in competitive MM environments.",
    }


# ---------------------------------------------------------------------------
# Tests: Taxonomy
# ---------------------------------------------------------------------------

class TestTaxonomyLoad:

    def test_six_families_present(self):
        families = load_taxonomy()
        expected = {
            "queue_position_mm",
            "second_wave_ofi_momentum",
            "cross_product_lead_lag",
            "toxicity_provision_overlay",
            "hawkes_cascade_triggers",
            "gex_dealer_gamma_overlay",
        }
        assert expected == set(families.keys())

    def test_each_family_has_required_keys(self):
        families = load_taxonomy()
        required_keys = {"description", "allowed_feature_inputs", "forbidden", "template_slots"}
        for name, spec in families.items():
            missing = required_keys - set(spec.keys())
            assert not missing, f"Family '{name}' missing keys: {missing}"

    def test_allowed_feature_inputs_are_lists(self):
        families = load_taxonomy()
        for name, spec in families.items():
            assert isinstance(spec["allowed_feature_inputs"], list), \
                f"Family '{name}': allowed_feature_inputs must be a list"
            assert len(spec["allowed_feature_inputs"]) > 0, \
                f"Family '{name}': allowed_feature_inputs must not be empty"

    def test_forbidden_lists_are_lists(self):
        families = load_taxonomy()
        for name, spec in families.items():
            assert isinstance(spec["forbidden"], list), \
                f"Family '{name}': forbidden must be a list"

    def test_no_first_wave_race_in_allowed_inputs(self):
        """None of the families should allow first-wave race features."""
        families = load_taxonomy()
        for name, spec in families.items():
            for feat in spec["allowed_feature_inputs"]:
                assert "first_wave_race" not in feat.lower(), \
                    f"Family '{name}' allows forbidden feature: {feat}"


# ---------------------------------------------------------------------------
# Tests: Quota enforcement
# ---------------------------------------------------------------------------

class TestQuotaEnforcement:

    def test_count_zero_when_no_log(self, tmp_path):
        log_path = tmp_path / "intake_log.jsonl"
        assert count_recent_candidates(log_path) == 0

    def test_count_zero_when_empty_log(self, tmp_path):
        log_path = tmp_path / "intake_log.jsonl"
        log_path.write_text("", encoding="utf-8")
        assert count_recent_candidates(log_path) == 0

    def test_counts_recent_records(self, tmp_path):
        log_path = tmp_path / "intake_log.jsonl"
        # 3 records today
        _make_log_with_n_recent(log_path, 3, days_ago=0.0)
        assert count_recent_candidates(log_path) == 3

    def test_ignores_old_records(self, tmp_path):
        log_path = tmp_path / "intake_log.jsonl"
        # 4 records from 8 days ago (outside 7-day window) + 1 today
        records = [
            _make_log_record(days_ago=8.0, hypothesis_id=f"SLOWTIER-old-{i:03d}")
            for i in range(1, 5)
        ] + [_make_log_record(days_ago=0.0, hypothesis_id="SLOWTIER-new-001")]
        log_path.write_text("\n".join(records) + "\n", encoding="utf-8")
        assert count_recent_candidates(log_path) == 1

    def test_exactly_at_boundary_counted(self, tmp_path):
        """Records exactly 7 days old (604800s) are at boundary; counted."""
        log_path = tmp_path / "intake_log.jsonl"
        # 6 days 23h 59m ago — inside the 7-day window
        _make_log_with_n_recent(log_path, 1, days_ago=6.999)
        assert count_recent_candidates(log_path) == 1

    def test_run_intake_exits_3_when_disabled(self, tmp_path):
        cfg = _make_cfg(tmp_path, enabled=False)
        exit_code, msg = run_intake(
            family_name="queue_position_mm",
            trade_date="2026-06-12",
            model="gemma4-test",
            cfg=cfg,
        )
        assert exit_code == 3
        assert "F3 disabled" in msg

    def test_run_intake_exits_4_when_quota_reached(self, tmp_path):
        cfg = _make_cfg(tmp_path, enabled=True, weekly_quota=5)
        log_path = Path(cfg.artifact_root) / "intake_log.jsonl"
        _make_log_with_n_recent(log_path, 5, days_ago=0.0)  # exactly 5 = quota

        exit_code, msg = run_intake(
            family_name="queue_position_mm",
            trade_date="2026-06-12",
            model="gemma4-test",
            cfg=cfg,
        )
        assert exit_code == 4
        assert "weekly quota reached" in msg
        assert "5/5" in msg

    def test_run_intake_proceeds_when_below_quota(self, tmp_path):
        """4 out of 5 quota used — intake should proceed (needs model)."""
        cfg = _make_cfg(tmp_path, enabled=True, weekly_quota=5)
        log_path = Path(cfg.artifact_root) / "intake_log.jsonl"
        _make_log_with_n_recent(log_path, 4, days_ago=0.0)

        with patch("llm_slow_tier.src.intake.ollama_call") as mock_call:
            mock_call.return_value = (json.dumps(_valid_candidate()), None)
            exit_code, msg = run_intake(
                family_name="queue_position_mm",
                trade_date="2026-06-12",
                model="gemma4-test",
                cfg=cfg,
            )
        # Should succeed (exit 0) or fail on hypothesis validation, not on quota
        assert exit_code != 4, "Should not have hit quota with only 4/5 used"

    def test_custom_quota_respected(self, tmp_path):
        """Weekly quota of 2: 2 existing records should trigger exit 4."""
        cfg = _make_cfg(tmp_path, enabled=True, weekly_quota=2)
        log_path = Path(cfg.artifact_root) / "intake_log.jsonl"
        _make_log_with_n_recent(log_path, 2, days_ago=0.0)

        exit_code, msg = run_intake(
            family_name="queue_position_mm",
            trade_date="2026-06-12",
            model="gemma4-test",
            cfg=cfg,
        )
        assert exit_code == 4


# ---------------------------------------------------------------------------
# Tests: parse_candidate_json
# ---------------------------------------------------------------------------

class TestParseCandidateJson:

    def test_valid_candidate_parsed(self):
        candidate, error = parse_candidate_json(json.dumps(_valid_candidate()))
        assert error is None
        assert candidate is not None
        assert candidate["statement"].startswith("Queue position")

    def test_invalid_json_returns_error(self):
        candidate, error = parse_candidate_json("not json {}")
        assert candidate is None
        assert "json_decode_error" in error

    def test_missing_key_returns_error(self):
        d = _valid_candidate()
        del d["pass_criteria"]
        candidate, error = parse_candidate_json(json.dumps(d))
        assert candidate is None
        assert "missing_keys" in error
        assert "pass_criteria" in error

    def test_not_object_returns_error(self):
        candidate, error = parse_candidate_json(json.dumps([1, 2, 3]))
        assert candidate is None
        assert "not_a_json_object" in error


# ---------------------------------------------------------------------------
# Tests: build_and_validate_hypothesis
# ---------------------------------------------------------------------------

class TestBuildAndValidateHypothesis:

    def test_hypothesis_id_set_correctly(self):
        candidate = _valid_candidate()
        hyp, flags = build_and_validate_hypothesis(candidate, "SLOWTIER-2026-06-12-001")
        assert hyp.hypothesis_id == "SLOWTIER-2026-06-12-001"

    def test_statement_mapped(self):
        candidate = _valid_candidate()
        hyp, _ = build_and_validate_hypothesis(candidate, "SLOWTIER-test-001")
        assert hyp.statement == candidate["statement"]

    def test_threshold_mapped_as_float(self):
        candidate = _valid_candidate()
        hyp, _ = build_and_validate_hypothesis(candidate, "SLOWTIER-test-001")
        assert hyp.threshold == pytest.approx(0.80)

    def test_pass_fail_criteria_mapped(self):
        candidate = _valid_candidate()
        hyp, _ = build_and_validate_hypothesis(candidate, "SLOWTIER-test-001")
        assert hyp.pass_criteria == candidate["pass_criteria"]
        assert hyp.fail_criteria == candidate["fail_criteria"]

    def test_quarantine_flags_are_list(self):
        candidate = _valid_candidate()
        _, flags = build_and_validate_hypothesis(candidate, "SLOWTIER-test-001")
        assert isinstance(flags, list)

    def test_none_threshold_handled(self):
        candidate = _valid_candidate()
        candidate["threshold"] = None
        hyp, _ = build_and_validate_hypothesis(candidate, "SLOWTIER-test-001")
        assert hyp.threshold is None


# ---------------------------------------------------------------------------
# Tests: write_candidate
# ---------------------------------------------------------------------------

class TestWriteCandidate:

    def test_candidate_json_written(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        candidate = _valid_candidate()
        hyp, flags = build_and_validate_hypothesis(candidate, "SLOWTIER-2026-06-12-001")
        cpath, lpath = write_candidate(
            hypothesis=hyp,
            candidate=candidate,
            family_name="queue_position_mm",
            quarantine_flags=flags,
            artifact_root=cfg.artifact_root,
            model="gemma4-test",
            trade_date="2026-06-12",
        )
        assert cpath.exists()
        payload = json.loads(cpath.read_text(encoding="utf-8"))
        assert payload["hypothesis_id"] == "SLOWTIER-2026-06-12-001"
        assert payload["family"] == "queue_position_mm"
        assert payload["status"] == "quarantine"

    def test_log_record_appended(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        candidate = _valid_candidate()
        hyp, flags = build_and_validate_hypothesis(candidate, "SLOWTIER-2026-06-12-002")
        _, lpath = write_candidate(
            hypothesis=hyp,
            candidate=candidate,
            family_name="queue_position_mm",
            quarantine_flags=flags,
            artifact_root=cfg.artifact_root,
            model="gemma4-test",
            trade_date="2026-06-12",
        )
        lines = [l for l in lpath.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["hypothesis_id"] == "SLOWTIER-2026-06-12-002"
        assert rec["family"] == "queue_position_mm"
        assert "generated_utc" in rec
        assert rec["model"] == "gemma4-test"

    def test_quarantine_flags_in_log_and_file(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        candidate = _valid_candidate()
        hyp, _ = build_and_validate_hypothesis(candidate, "SLOWTIER-test-003")
        explicit_flags = ["test_quarantine_flag"]
        _, lpath = write_candidate(
            hypothesis=hyp,
            candidate=candidate,
            family_name="queue_position_mm",
            quarantine_flags=explicit_flags,
            artifact_root=cfg.artifact_root,
            model="gemma4-test",
            trade_date="2026-06-12",
        )
        rec = json.loads(lpath.read_text(encoding="utf-8").strip())
        assert "test_quarantine_flag" in rec["quarantine_flags"]

    def test_candidate_in_research_inputs_slow_tier(self, tmp_path):
        """Candidate must land in research_inputs/slow_tier/, not in features_engine."""
        cfg = _make_cfg(tmp_path)
        candidate = _valid_candidate()
        hyp, flags = build_and_validate_hypothesis(candidate, "SLOWTIER-test-004")
        cpath, _ = write_candidate(
            hypothesis=hyp,
            candidate=candidate,
            family_name="queue_position_mm",
            quarantine_flags=flags,
            artifact_root=cfg.artifact_root,
            model="gemma4-test",
            trade_date="2026-06-12",
        )
        # Must be under research_inputs/slow_tier/ — never features_engine
        parts = cpath.parts
        assert "research_inputs" in parts
        assert "slow_tier" in parts
        assert "features_engine" not in parts


# ---------------------------------------------------------------------------
# Tests: run_intake end-to-end (mocked ollama)
# ---------------------------------------------------------------------------

class TestRunIntakeEndToEnd:

    def test_full_success_path(self, tmp_path):
        cfg = _make_cfg(tmp_path, enabled=True, weekly_quota=5)

        with patch("llm_slow_tier.src.intake.ollama_call") as mock_call:
            mock_call.return_value = (json.dumps(_valid_candidate()), None)
            exit_code, msg = run_intake(
                family_name="queue_position_mm",
                trade_date="2026-06-12",
                model="gemma4-test",
                cfg=cfg,
            )

        assert exit_code == 0
        assert "SLOWTIER-2026-06-12" in msg
        # Verify the log file was written
        log_path = Path(cfg.artifact_root) / "intake_log.jsonl"
        assert log_path.exists()

    def test_unknown_family_exits_1(self, tmp_path):
        cfg = _make_cfg(tmp_path, enabled=True)
        exit_code, msg = run_intake(
            family_name="nonexistent_family",
            trade_date="2026-06-12",
            model="gemma4-test",
            cfg=cfg,
        )
        assert exit_code == 1
        assert "unknown family" in msg

    def test_ollama_error_exits_1(self, tmp_path):
        cfg = _make_cfg(tmp_path, enabled=True)
        with patch("llm_slow_tier.src.intake.ollama_call") as mock_call:
            mock_call.return_value = (None, "model not found in ollama list")
            exit_code, msg = run_intake(
                family_name="queue_position_mm",
                trade_date="2026-06-12",
                model="gemma4-test",
                cfg=cfg,
            )
        assert exit_code == 1
        assert "ollama error" in msg

    def test_message_mentions_no_auto_registration(self, tmp_path):
        """The output must explicitly state that promotion is human-only."""
        cfg = _make_cfg(tmp_path, enabled=True)
        with patch("llm_slow_tier.src.intake.ollama_call") as mock_call:
            mock_call.return_value = (json.dumps(_valid_candidate()), None)
            exit_code, msg = run_intake(
                family_name="queue_position_mm",
                trade_date="2026-06-12",
                model="gemma4-test",
                cfg=cfg,
            )
        assert exit_code == 0
        assert "human" in msg.lower() or "gauntlet" in msg.lower()

    def test_hypothesis_id_uses_date_and_seq(self, tmp_path):
        """hypothesis_id must follow the SLOWTIER-{date}-{seq} pattern."""
        cfg = _make_cfg(tmp_path, enabled=True)
        with patch("llm_slow_tier.src.intake.ollama_call") as mock_call:
            mock_call.return_value = (json.dumps(_valid_candidate()), None)
            exit_code, msg = run_intake(
                family_name="queue_position_mm",
                trade_date="2026-06-12",
                model="gemma4-test",
                cfg=cfg,
            )
        assert exit_code == 0
        assert "SLOWTIER-2026-06-12-" in msg
