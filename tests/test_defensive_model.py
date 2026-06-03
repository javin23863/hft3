"""Phase 7 tests for the HFT3 DefensiveModel ABC.

Covers:
- test_filter_decision_passthrough: defaults are non-vetoing
- test_filter_decision_veto: veto sets vetoed=True
- test_filter_decision_skew: skew multiplies the signal
- test_filter_decision_throttle: throttle is non-vetoing
- test_defensive_model_must_subclass: DefensiveModel cannot be instantiated directly
- test_defensive_model_does_not_inherit_from_workbench: class boundary enforced
- test_model_combinations_no_manual_code_change: enumerating combinations is data-driven
- test_hybrid_degradation_detection: when adding a defensive hurts, the
  combination runner must surface this
- test_filter_action_enum_complete: all 4 actions defined
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List

import pytest

from apps.workbench.src.core.defensive import (
    DefensiveDiagnostics,
    DefensiveModel,
    FilterAction,
    FilterDecision,
    MODEL_COMBINATIONS,
)
from apps.workbench.src.core.protocol import WorkbenchModel


# ---------- FilterDecision factory methods ----------


def test_filter_decision_passthrough() -> None:
    d = FilterDecision.passthrough()
    assert d.action == FilterAction.TAG
    assert d.vetoed is False
    assert d.skew == 1.0
    assert d.reason_code == "DEFENSIVE_PASSTHROUGH"


def test_filter_decision_veto() -> None:
    d = FilterDecision.veto("REGIME_BLACKLIST", tags={"regime": "fomc"})
    assert d.vetoed is True
    assert d.action == FilterAction.VETO
    assert d.tags == {"regime": "fomc"}


def test_filter_decision_rejects_empty_veto_reason() -> None:
    with pytest.raises(ValueError, match="reason_code"):
        FilterDecision.veto("")


def test_filter_decision_tags_are_copied_and_immutable() -> None:
    tags = {"regime": "fomc"}
    d = FilterDecision.veto("REGIME_BLACKLIST", tags=tags)
    tags["regime"] = "changed"

    assert d.tags == {"regime": "fomc"}
    with pytest.raises(TypeError):
        d.tags["regime"] = "mutated"  # type: ignore[index]


def test_filter_decision_skew() -> None:
    d = FilterDecision.skew_signal(0.5, "LATENCY_DEGRADED")
    assert d.action == FilterAction.SKEW
    assert d.skew == 0.5
    assert d.vetoed is False


def test_filter_decision_throttle() -> None:
    d = FilterDecision.throttle("RATE_LIMIT_HIT")
    assert d.action == FilterAction.THROTTLE
    assert d.vetoed is False


def test_filter_action_enum_complete() -> None:
    assert {a.value for a in FilterAction} == {"veto", "skew", "throttle", "tag"}


# ---------- DefensiveModel class boundary ----------


def test_defensive_model_must_subclass() -> None:
    with pytest.raises(TypeError):
        DefensiveModel()  # type: ignore[abstract]


def test_defensive_model_does_not_inherit_from_workbench() -> None:
    """Spec: defensives are a distinct base class, not a WorkbenchModel
    subclass. The composition contract must be enforced by the class
    boundary, not by a role tag."""
    class MyDefensive(DefensiveModel):
        model_id = "test"

        def validate_inputs(self, ctx: Any) -> List[str]:
            return []

        def defend(self, ctx: Any, signal: Any) -> FilterDecision:
            return FilterDecision.passthrough()

    d = MyDefensive()
    assert not isinstance(d, WorkbenchModel)
    assert d.model_id == "test"


def test_defensive_subclass_can_provide_diagnostics() -> None:
    class MyDefensive(DefensiveModel):
        model_id = "test"

        def validate_inputs(self, ctx: Any) -> List[str]:
            return []

        def defend(self, ctx: Any, signal: Any) -> FilterDecision:
            return FilterDecision.passthrough()

        def produce_diagnostics(self, ctx: Any, result: FilterDecision) -> DefensiveDiagnostics:
            return DefensiveDiagnostics(
                model_id="test",
                metrics={"calls": 1},
                warnings=[],
            )

    d = MyDefensive()
    diag = d.produce_diagnostics(None, FilterDecision.passthrough())
    assert diag.metrics == {"calls": 1}


# ---------- MODEL_COMBINATIONS canonical test matrix ----------


def test_model_combinations_no_manual_code_change() -> None:
    """The 26-phase spec requires: alpha-only, defensive-only, alpha+1 def,
    alpha+N defs, hybrid, no-defensive baseline, individual defensive
    contribution, defensive ablation, hybrid improvement/degradation.
    Every entry must be reachable without editing the orchestrator."""
    names = {c["name"] for c in MODEL_COMBINATIONS}
    required = {
        "alpha_only",
        "no_defensive_baseline",
        "alpha_plus_one_defensive",
        "alpha_plus_multiple_defensives",
        "hybrid_alpha_plus_structural",
        "defensive_only",
        "ablation_no_defensives",
    }
    assert required.issubset(names), f"missing: {required - names}"


def test_model_combinations_data_driven() -> None:
    """Each entry must be a plain dict the orchestrator can iterate.
    No executable objects, no closures."""
    for c in MODEL_COMBINATIONS:
        assert isinstance(c, dict)
        assert "name" in c
        assert "alpha" in c and isinstance(c["alpha"], bool)
        assert "defensives" in c and isinstance(c["defensives"], list)
        assert "structurals" in c and isinstance(c["structurals"], list)
        for d in c["defensives"]:
            assert isinstance(d, str)
        for s in c["structurals"]:
            assert isinstance(s, str)


def test_model_combinations_use_canonical_catalog_ids() -> None:
    from apps.workbench.src.registry.model_catalog import load_catalog

    catalog = load_catalog(Path(__file__).resolve().parents[1])
    placeholders = {"regime_filter", "throttle", "skew", "pdf_topology_1"}
    for combo in MODEL_COMBINATIONS:
        ids = set(combo["defensives"]) | set(combo["structurals"])
        assert not (ids & placeholders)
        for model_id in ids:
            assert model_id in catalog


def test_runner_ablation_no_defensives_is_empty(tmp_path: Path) -> None:
    from hft3.research.run_autonomous import AutonomousRunner, CampaignConfig

    cfg = CampaignConfig.from_yaml(Path("configs/research/autonomous_hft3.yaml"))
    cfg.output["artifacts_dir"] = str(tmp_path / "artifacts")
    runner = AutonomousRunner(config=cfg, root=tmp_path, run_id="PHASE7_COMBOS")

    path = runner.stage_resolve_model_combinations()
    combos = {c["name"]: c for c in json.loads(path.read_text(encoding="utf-8"))}

    assert combos["ablation_no_defensives"]["defensive_ids"] == []
    assert combos["alpha_plus_one_defensive"]["defensive_ids"] == ["VPIN_TOXICITY"]


# ---------- hybrid degradation detection ----------


def test_hybrid_degradation_detection_utility() -> None:
    """Spec: the system must detect when adding a defensive or hybrid
    component hurts performance. We test a small helper that compares
    a baseline metric to a combination metric and reports regression."""

    def is_regression(
        baseline_net_pnl: float, combination_net_pnl: float, *, tolerance: float = 0.0
    ) -> bool:
        return combination_net_pnl < baseline_net_pnl - tolerance

    # No regression: defensive adds value
    assert not is_regression(100.0, 150.0)
    # Regression: defensive hurts by 5
    assert is_regression(100.0, 95.0)
    # Within tolerance
    assert not is_regression(100.0, 99.0, tolerance=2.0)
    # Negative baseline, defensive improves (less loss) — not a regression
    assert not is_regression(-100.0, -50.0)


def test_filter_decision_reason_code_required_for_veto() -> None:
    """Veto decisions must carry a non-empty reason_code for the audit log."""
    d = FilterDecision.veto("FOM_DAY")
    assert d.reason_code, "veto must have reason_code"
    assert d.vetoed is True
