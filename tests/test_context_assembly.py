"""Macro, continuous session, and latency_state assembly tests (Phase 4)."""

from __future__ import annotations

from replay.context_assembly import (
    CONTINUOUS_CLOCK,
    apply_continuous_session_to_recipe_family,
    apply_latency_to_recipe_family,
    apply_macro_to_recipe_family,
    enrich_context_snapshot,
    validate_continuous_session,
    validate_latency_state,
    validate_macro_context,
)


def test_macro_context_active_event() -> None:
    snap = enrich_context_snapshot(
        {"event_context": "CPI_TIGHT", "target_event_id": "CPI_2024_09_11_TIGHT"},
        source_timestamp_ns=1000,
    )
    result = validate_macro_context(snap, decision_timestamp_ns=2000)
    assert result.ok
    assert result.event_context == "CPI_TIGHT"


def test_macro_rejects_future_source_timestamp() -> None:
    snap = enrich_context_snapshot({"event_context": "NFP_TIGHT"}, source_timestamp_ns=3000)
    result = validate_macro_context(snap, decision_timestamp_ns=2000)
    assert not result.ok
    assert "future_macro_source_timestamp" in result.reasons


def test_continuous_session_out_of_scope_for_scheduled_clock() -> None:
    snap = enrich_context_snapshot(
        {"session_features": {"distance_to_vwap": 1.0}},
        source_timestamp_ns=1000,
    )
    result = validate_continuous_session(snap, research_clock="scheduled_event")
    assert not result.ok
    assert not result.in_scope


def test_continuous_session_in_scope_with_features() -> None:
    snap = enrich_context_snapshot(
        {"session_features": {"distance_to_vwap": 2.0, "is_breaking_session_level": 0.0}},
        source_timestamp_ns=1000,
    )
    result = validate_continuous_session(
        snap,
        research_clock=CONTINUOUS_CLOCK,
        decision_timestamp_ns=2000,
    )
    assert result.ok
    assert "distance_to_vwap" in result.session_features_present


def test_latency_state_requires_non_negative_fields() -> None:
    snap = {"order_latency_ms": 1.5, "feature_latency_ms": 2.0}
    result = validate_latency_state(snap)
    assert result.ok

    bad = validate_latency_state({"order_latency_ms": -1.0, "feature_latency_ms": 1.0})
    assert not bad.ok
    assert "negative_order_latency_ms" in bad.reasons


def test_apply_context_families_updates_recipe_rows() -> None:
    macro = validate_macro_context(
        enrich_context_snapshot({"event_context": "CPI_TIGHT"}, source_timestamp_ns=1000),
        decision_timestamp_ns=2000,
    )
    macro_fam: dict = {"family_id": "macro_context"}
    apply_macro_to_recipe_family(macro_fam, macro)
    assert macro_fam["pit_proof"] == "declared"

    latency = validate_latency_state({"order_latency_ms": 1.0, "feature_latency_ms": 1.0})
    latency_fam: dict = {"family_id": "latency_state"}
    apply_latency_to_recipe_family(latency_fam, latency)
    assert "order_latency_ms" in latency_fam["selected_features"]

    session = validate_continuous_session(
        enrich_context_snapshot({"session_features": {"distance_to_vwap": 1.0}}, source_timestamp_ns=1000),
        research_clock=CONTINUOUS_CLOCK,
        decision_timestamp_ns=2000,
    )
    session_fam: dict = {"family_id": "continuous_session"}
    apply_continuous_session_to_recipe_family(session_fam, session)
    assert session_fam["model_consumption_state"] == "not_measured"
