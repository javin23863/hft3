"""Tests for the central semantic-execution contract layer (no-cherry-pick v2).

Coverage is derived from the registry loader, never a hard-coded literal: the
whole point of the fix is that every canonical slug is ledgered and only
semantically executable standalone strategies enter the order queue.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from backtest_pipeline.src import model_execution_contracts as mec  # noqa: E402
from features_engine.src.model_registry import all_slugs, load_model_registry  # noqa: E402

# Inventory guard. These numbers are the current registry partition; they are
# asserted so an accidental slug add/drop is caught, but they derive from the
# registry (computed below) — update intentionally, never silently.
_EXPECTED_TOTAL = 65
_EXPECTED_BY_KIND = {"hypothesis": 50, "pdf_structural": 11, "reinforcement_learning": 4}


def test_coverage_equals_all_slugs() -> None:
    contracts = mec.all_contracts()
    assert set(contracts) == set(all_slugs())
    # count derives from the loader, not a stale literal
    assert len(contracts) == len(all_slugs())


def test_inventory_partition_matches_registry() -> None:
    models = load_model_registry().get("models", {})
    live = Counter(str(e.get("kind")) for e in models.values())
    assert sum(live.values()) == _EXPECTED_TOTAL
    assert dict(live) == _EXPECTED_BY_KIND
    # every contract's kind agrees with the registry
    contracts = mec.all_contracts()
    assert Counter(c.kind for c in contracts.values()) == _EXPECTED_BY_KIND


def test_every_contract_has_valid_role_and_consistent_policy() -> None:
    for slug, c in mec.all_contracts().items():
        assert c.execution_role in mec.EXECUTION_ROLES, slug
        assert c.standalone_hbt_policy in mec.STANDALONE_POLICIES, slug
        # policy is a pure function of role — no drift
        assert c.standalone_hbt_policy == mec._ROLE_TO_POLICY[c.execution_role], slug


def test_no_unknown_semantic_contract() -> None:
    # every slug classifies; _build_all raises on an unclassifiable slug
    assert mec.all_contracts()  # non-empty, built without raising


@pytest.mark.parametrize(
    "slug,role",
    [
        ("HAWKES_TOXIC_FLOW", "defensive_overlay"),
        ("VPIN_TOXICITY", "defensive_overlay"),
        ("QUANTUM_SPREAD_DEFENSE", "defensive_overlay"),
        ("HYBRID_EXECUTION", "execution_engine"),
        ("DEALER_HEDGING", "options_fixture"),
        ("BOOK_PRESSURE", "context_feature"),
    ],
)
def test_pdf_structural_never_standalone(slug: str, role: str) -> None:
    c = mec.model_execution_contract(slug)
    assert c.kind == "pdf_structural"
    assert c.execution_role == role
    assert not c.is_standalone_alpha, f"{slug} must not enter the standalone order queue"


def test_defensive_hypothesis_not_standalone() -> None:
    c = mec.model_execution_contract("QUOTE_PULL_BEFORE_VOLATILITY")
    assert c.kind == "hypothesis"
    assert c.blocks_trade is True
    assert c.execution_role == "defensive_overlay"
    assert not c.is_standalone_alpha


def test_cross_asset_requires_leader_tape() -> None:
    c = mec.model_execution_contract("ES_MES_LEAD_LAG")
    assert c.execution_role == "cross_asset_primary_alpha"
    assert c.standalone_hbt_policy == "requires_leader_tape"
    assert c.required_leaders == ("ES",)
    assert c.target_instrument_universe == ("MES",)
    # role is order-producing; tape presence is checked downstream
    assert c.is_standalone_alpha


def test_sensor_model_requires_sensor_tape() -> None:
    c = mec.model_execution_contract("VIX_SPIKE_EVENT_FADE")
    assert c.execution_role == "sensor_conditioned_primary_alpha"
    assert c.standalone_hbt_policy == "requires_sensor_tape"
    assert c.required_sensors == ("VIX",)


def test_rl_models_blocked() -> None:
    for slug in (
        "RL_EXECUTION_POLICY",
        "RL_DEEP_Q_EXECUTION_PROXY",
        "RL_VIX_OPTIONS_CLUE_PROXY",
        "RL_PPO_SIM_POLICY",
    ):
        c = mec.model_execution_contract(slug)
        assert c.kind == "reinforcement_learning"
        assert c.execution_role == "rl_research_blocked"
        assert c.standalone_hbt_policy == "blocked_not_order_strategy"
        assert not c.is_standalone_alpha


def test_plain_hypothesis_is_standalone() -> None:
    c = mec.model_execution_contract("SECOND_WAVE_CONTINUATION")
    assert c.execution_role == "primary_alpha"
    assert c.standalone_hbt_policy == "standalone_executable"
    assert c.is_standalone_alpha


def test_router_shares_one_taxonomy() -> None:
    # fix-1: the router must read the contract module's sets, not a parallel copy
    from backtest_pipeline.src import pipeline_model_router as router

    assert router.PDF_DIAGNOSTICS is mec.PDF_DIAGNOSTICS
    assert router.PDF_STRUCTURAL_EVAL is mec.PDF_STRUCTURAL_EVAL
    assert router.PDF_HYBRID_REPLAY is mec.PDF_HYBRID_REPLAY
    assert router.PDF_OPTIONS_FIXTURE is mec.PDF_OPTIONS_FIXTURE


def test_unknown_slug_fails_closed() -> None:
    with pytest.raises(KeyError):
        mec.model_execution_contract("NOT_A_REAL_MODEL")
