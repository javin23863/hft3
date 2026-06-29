"""Model registry slug parity tests."""
from __future__ import annotations

from collections import Counter

import pytest

from features_engine.src.model_registry import (
    all_slugs,
    get_hyp_id_for_slug,
    get_slug_for_hyp_id,
    legacy_to_slug,
    load_model_registry,
    resolve_model_id,
)

_REQUIRED_RL_MODEL_SLUGS = {
    "RL_EXECUTION_POLICY",
    "RL_DEEP_Q_EXECUTION_PROXY",
    "RL_VIX_OPTIONS_CLUE_PROXY",
    "RL_PPO_SIM_POLICY",
}


def test_registry_slugs_are_unique() -> None:
    slugs = all_slugs()
    assert len(slugs) == len(set(slugs))
    assert _REQUIRED_RL_MODEL_SLUGS.issubset(set(slugs))


def test_legacy_to_slug_bijection() -> None:
    models = load_model_registry()["models"]
    l2s = legacy_to_slug()
    legacy_count = sum(1 for entry in models.values() if entry.get("legacy_id"))
    assert len(l2s) == legacy_count
    assert len(set(l2s.values())) == legacy_count


def test_hyp_id_round_trip() -> None:
    for hyp_id in range(1, 51):
        slug = get_slug_for_hyp_id(hyp_id)
        assert get_hyp_id_for_slug(slug) == hyp_id


def test_resolve_legacy_warns() -> None:
    with pytest.warns(DeprecationWarning, match="legacy"):
        assert resolve_model_id("HYP_5") == "SPREAD_BLOWOUT_RECOMPRESSION"


def test_registry_entries_complete() -> None:
    models = load_model_registry()["models"]
    kind_counts = Counter(e.get("kind") for e in models.values())
    assert kind_counts["hypothesis"] == 50
    assert kind_counts["pdf_structural"] == 11
    assert kind_counts["reinforcement_learning"] >= len(_REQUIRED_RL_MODEL_SLUGS)


def test_reinforcement_learning_entries_are_carried_forward() -> None:
    models = load_model_registry()["models"]
    rl_entries = {
        slug: entry
        for slug, entry in models.items()
        if entry.get("kind") == "reinforcement_learning"
    }

    assert _REQUIRED_RL_MODEL_SLUGS.issubset(set(rl_entries))
    with pytest.warns(DeprecationWarning, match="legacy"):
        assert resolve_model_id("RL_EXECUTION") == "RL_EXECUTION_POLICY"
    with pytest.warns(DeprecationWarning, match="legacy"):
        assert resolve_model_id("RL_DEEP_Q_PROXY") == "RL_DEEP_Q_EXECUTION_PROXY"
    with pytest.warns(DeprecationWarning, match="legacy"):
        assert resolve_model_id("RL_VIX_OPTIONS_CLUE") == "RL_VIX_OPTIONS_CLUE_PROXY"
    with pytest.warns(DeprecationWarning, match="legacy"):
        assert resolve_model_id("RL_PPO_SIM") == "RL_PPO_SIM_POLICY"
    assert {
        entry.get("promotion_status")
        for entry in rl_entries.values()
    } == {"blocked_downstream_validation_required"}
    assert rl_entries["RL_PPO_SIM_POLICY"]["rl_status"] == "blocked_sim_env_required"
