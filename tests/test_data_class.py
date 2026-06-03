"""Phase 6 tests for the HFT3 data-resolution tagging.

Covers:
- test_data_class_enum_complete: 5 classes defined
- test_data_class_rank_ordering: ranks are strictly monotonic
- test_make_tag_eligible_when_classes_match
- test_make_tag_demoted_when_resolved_rank_lower
- test_make_tag_ineligible_when_synthetic
- test_make_tag_validates_reason_when_mismatched
- test_downgrade_reason_stable_codes
- test_promotion_eligibility_enum
- test_validity_impact_enum
- test_to_gate_result_eligible
- test_to_gate_result_ineligible_blocks
- test_data_resolution_tag_round_trip
- test_runner_writes_data_resolution_json: the runner writes the
  Phase 12 artifact with the correct tag fields
- test_runner_demotes_on_downgrade: when requested != resolved, the
  runner tags the run as DEMOTED and the data-eligibility gate is
  WARN severity (not BLOCKING)
- test_runner_blocks_on_synthetic: when resolved is SYNTHETIC_OR_DEGRADED,
  the gate is BLOCKING and the runner cannot PROMOTE
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hft3.data_class import (
    REASON_FALLBACK_TO_SYNTHETIC,
    REASON_NO_DOWNGRADE,
    REASON_REQUESTED_UNAVAILABLE,
    DataClass,
    DataResolutionTag,
    PromotionEligibility,
    ValidityImpact,
    data_class_rank,
    make_tag,
    to_gate_result,
)
from hft3.research.run_autonomous import AutonomousRunner, CampaignConfig
from hft3.validation.gate_result import GateCategory, Severity


# ---------- enum completeness ----------


def test_data_class_enum_complete() -> None:
    expected = {
        "L3_MBO", "L3_ORDERBOOK_SNAPSHOT", "TRADES_ONLY",
        "AGGREGATED_BARS", "SYNTHETIC_OR_DEGRADED",
    }
    assert {c.value for c in DataClass} == expected
    assert len(DataClass) == 5


def test_data_class_rank_ordering() -> None:
    """L3_MBO > L3_ORDERBOOK_SNAPSHOT > TRADES_ONLY > AGGREGATED_BARS > SYNTHETIC."""
    assert data_class_rank(DataClass.L3_MBO) > data_class_rank(DataClass.L3_ORDERBOOK_SNAPSHOT)
    assert data_class_rank(DataClass.L3_ORDERBOOK_SNAPSHOT) > data_class_rank(DataClass.TRADES_ONLY)
    assert data_class_rank(DataClass.TRADES_ONLY) > data_class_rank(DataClass.AGGREGATED_BARS)
    assert data_class_rank(DataClass.AGGREGATED_BARS) > data_class_rank(DataClass.SYNTHETIC_OR_DEGRADED)


def test_promotion_eligibility_enum() -> None:
    assert {e.value for e in PromotionEligibility} == {"eligible", "demoted", "ineligible"}


def test_validity_impact_enum() -> None:
    assert {e.value for e in ValidityImpact} == {"info", "warn", "blocking"}


# ---------- make_tag factory ----------


def test_make_tag_eligible_when_classes_match() -> None:
    tag = make_tag("L3_MBO", "L3_MBO", source="databento", symbols=["ES"])
    assert tag.promotion_eligibility_impact == PromotionEligibility.ELIGIBLE
    assert tag.validity_impact == ValidityImpact.INFO
    assert tag.downgrade_reason == REASON_NO_DOWNGRADE
    assert tag.is_mismatch is False
    assert tag.is_downgrade is False


def test_make_tag_demoted_when_resolved_rank_lower() -> None:
    tag = make_tag("L3_MBO", "TRADES_ONLY", source="databento", symbols=["ES"])
    assert tag.promotion_eligibility_impact == PromotionEligibility.DEMOTED
    assert tag.validity_impact == ValidityImpact.WARN
    assert tag.downgrade_reason == REASON_REQUESTED_UNAVAILABLE
    assert tag.is_mismatch is True
    assert tag.is_downgrade is True


def test_make_tag_ineligible_when_synthetic() -> None:
    tag = make_tag("L3_MBO", "SYNTHETIC_OR_DEGRADED", source="mock")
    assert tag.promotion_eligibility_impact == PromotionEligibility.INELIGIBLE
    assert tag.validity_impact == ValidityImpact.BLOCKING
    assert tag.downgrade_reason == REASON_FALLBACK_TO_SYNTHETIC
    assert tag.is_mismatch is True
    assert tag.is_downgrade is True


def test_make_tag_validates_reason_when_mismatched() -> None:
    tag = DataResolutionTag(
        requested_data_class=DataClass.L3_MBO,
        resolved_data_class=DataClass.TRADES_ONLY,
        downgrade_reason=REASON_NO_DOWNGRADE,
    )
    with pytest.raises(ValueError):
        tag.validate()


def test_downgrade_reason_stable_codes() -> None:
    """The reason codes are stable UPPER_SNAKE_CASE identifiers."""
    for code in (
        REASON_REQUESTED_UNAVAILABLE,
        REASON_FALLBACK_TO_SYNTHETIC,
    ):
        assert code == code.upper()
        assert "_" in code


# ---------- gate derivation ----------


def test_to_gate_result_eligible() -> None:
    tag = make_tag("L3_MBO", "L3_MBO")
    g = to_gate_result(tag)
    assert g.gate_category == GateCategory.DATA_INTEGRITY
    assert g.severity == Severity.INFO
    assert g.blocking_status is False
    assert g.pass_fail is True
    assert g.observed_value == "eligible"
    assert g.reason_code == "DATA_ELIGIBLE"


def test_to_gate_result_demoted() -> None:
    tag = make_tag("L3_MBO", "TRADES_ONLY")
    g = to_gate_result(tag)
    assert g.severity == Severity.WARN
    assert g.blocking_status is False
    assert g.pass_fail is True  # demoted is still passable, just flagged


def test_to_gate_result_ineligible_blocks() -> None:
    tag = make_tag("L3_MBO", "SYNTHETIC_OR_DEGRADED")
    g = to_gate_result(tag)
    assert g.severity == Severity.BLOCKING
    assert g.blocking_status is True
    assert g.pass_fail is False
    assert g.observed_value == "ineligible"
    assert g.reason_code == "DATA_INELIGIBLE_FOR_PROMOTION"


# ---------- round-trip ----------


def test_data_resolution_tag_round_trip() -> None:
    tag = make_tag(
        "L3_MBO", "L3_ORDERBOOK_SNAPSHOT",
        source="databento",
        symbols=["ES", "MES"],
        time_windows=[(100, 200), (300, 400)],
    )
    d = tag.to_dict()
    tag2 = DataResolutionTag.from_dict(d)
    assert tag2.requested_data_class == tag.requested_data_class
    assert tag2.resolved_data_class == tag.resolved_data_class
    assert tag2.symbols_affected == tag.symbols_affected
    assert tag2.time_windows_affected == tag.time_windows_affected
    assert tag2.promotion_eligibility_impact == tag.promotion_eligibility_impact


def test_data_resolution_from_dict_requires_promotion_fields() -> None:
    raw = make_tag("L3_MBO", "SYNTHETIC_OR_DEGRADED").to_dict()
    raw.pop("promotion_eligibility_impact")

    with pytest.raises(ValueError, match="missing required fields"):
        DataResolutionTag.from_dict(raw)


def test_data_resolution_from_dict_rejects_inconsistent_degraded_tag() -> None:
    raw = make_tag("L3_MBO", "SYNTHETIC_OR_DEGRADED").to_dict()
    raw["promotion_eligibility_impact"] = "eligible"

    with pytest.raises(ValueError, match="ineligible for promotion"):
        DataResolutionTag.from_dict(raw)


# ---------- runner integration ----------


def _config_with_data(**data_overrides) -> CampaignConfig:
    base = CampaignConfig.from_yaml(Path("configs/research/autonomous_hft3.yaml"))
    base.data.update(data_overrides)
    return base


def test_runner_writes_data_resolution_json(tmp_path: Path) -> None:
    cfg = _config_with_data(requested="L3_MBO", resolved="L3_MBO")
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="DR1")
    runner.run()
    dr_path = tmp_path / "artifacts" / "DR1" / "data_resolution.json"
    assert dr_path.is_file()
    payload = json.loads(dr_path.read_text(encoding="utf-8"))
    assert payload["requested_data_class"] == "L3_MBO"
    assert payload["resolved_data_class"] == "L3_MBO"
    assert payload["promotion_eligibility_impact"] == "eligible"
    assert payload["downgrade_reason"] == ""


def test_runner_demotes_on_downgrade(tmp_path: Path) -> None:
    cfg = _config_with_data(requested="L3_MBO", resolved="TRADES_ONLY")
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="DR2")
    runner.run()
    dr_path = tmp_path / "artifacts" / "DR2" / "data_resolution.json"
    payload = json.loads(dr_path.read_text(encoding="utf-8"))
    assert payload["promotion_eligibility_impact"] == "demoted"
    assert payload["downgrade_reason"] == REASON_REQUESTED_UNAVAILABLE
    # The data-eligibility gate should be WARN (not BLOCKING)
    assert runner._data_eligibility_gate.severity == Severity.WARN
    assert runner._data_eligibility_gate.blocking_status is False


def test_runner_persists_data_gate_in_robustness_gates(tmp_path: Path) -> None:
    cfg = _config_with_data(requested="L3_MBO", resolved="TRADES_ONLY")
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="DR2GATE")
    runner.run()
    payload = json.loads(
        (tmp_path / "artifacts" / "DR2GATE" / "robustness_gates.json").read_text(encoding="utf-8")
    )
    gates = {gate["gate_name"]: gate for gate in payload["gates"]}

    assert gates["data_resolution_eligibility"]["pass_fail"] is True
    assert gates["data_resolution_eligibility"]["severity"] == "warn"
    assert gates["data_resolution_eligibility"]["observed_value"] == "demoted"


def test_runner_blocks_on_synthetic(tmp_path: Path) -> None:
    cfg = _config_with_data(requested="L3_MBO", resolved="SYNTHETIC_OR_DEGRADED")
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="DR3")
    runner.run()
    dr_path = tmp_path / "artifacts" / "DR3" / "data_resolution.json"
    payload = json.loads(dr_path.read_text(encoding="utf-8"))
    assert payload["promotion_eligibility_impact"] == "ineligible"
    # The data-eligibility gate should be BLOCKING
    assert runner._data_eligibility_gate.severity == Severity.BLOCKING
    assert runner._data_eligibility_gate.blocking_status is True
    assert runner._data_eligibility_gate.pass_fail is False


def test_runner_backward_compat_legacy_resolution_field(tmp_path: Path) -> None:
    """The legacy single `resolution` field still works: both requested
    and resolved are set to it."""
    cfg = _config_with_data(resolution="L3_MBO")
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="DR4")
    runner.run()
    dr_path = tmp_path / "artifacts" / "DR4" / "data_resolution.json"
    payload = json.loads(dr_path.read_text(encoding="utf-8"))
    assert payload["requested_data_class"] == "L3_MBO"
    assert payload["resolved_data_class"] == "L3_MBO"
    assert payload["promotion_eligibility_impact"] == "eligible"
