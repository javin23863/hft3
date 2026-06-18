"""Macro context, continuous session, and latency_state assembly for feature recipes."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, MutableMapping

from replay.cross_asset_assembly import PROVENANCE_SOURCE_TS

SESSION_FEATURE_KEYS = frozenset(
    {
        "distance_to_vwap",
        "is_breaking_session_level",
        "spread_stress_elevated",
        "distance_to_round_number",
    }
)

CONTINUOUS_CLOCK = "continuous_intraday"


def _value_state(value: Any) -> str:
    if value is None:
        return "missing"
    try:
        if math.isnan(float(value)):
            return "malformed"
    except (TypeError, ValueError):
        return "malformed"
    return "present"


def _source_ts(snapshot: Mapping[str, Any]) -> int | None:
    raw = snapshot.get(PROVENANCE_SOURCE_TS) or snapshot.get("source_timestamp_ns")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


@dataclass
class MacroContextValidation:
    ok: bool
    event_context: str | None
    target_event_id: str | None
    missingness_state: str
    reasons: list[str] = field(default_factory=list)
    source_timestamp_ns: int | None = None
    decision_timestamp_ns: int | None = None

    def to_proof(self) -> dict[str, Any]:
        return {
            "event_context": self.event_context,
            "target_event_id": self.target_event_id,
            "ok": self.ok,
            "missingness_state": self.missingness_state,
            "reasons": list(self.reasons),
            "source_timestamp_ns": self.source_timestamp_ns,
            "decision_timestamp_ns": self.decision_timestamp_ns,
        }


@dataclass
class ContinuousSessionValidation:
    ok: bool
    in_scope: bool
    missingness_state: str
    session_features_present: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    research_clock: str = "scheduled_event"
    decision_timestamp_ns: int | None = None

    def to_proof(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "in_scope": self.in_scope,
            "research_clock": self.research_clock,
            "missingness_state": self.missingness_state,
            "session_features_present": list(self.session_features_present),
            "reasons": list(self.reasons),
            "decision_timestamp_ns": self.decision_timestamp_ns,
        }


@dataclass
class LatencyStateValidation:
    ok: bool
    order_latency_ms: float | None
    feature_latency_ms: float | None
    latency_artifact_id: str | None
    missingness_state: str
    reasons: list[str] = field(default_factory=list)
    decision_timestamp_ns: int | None = None

    def to_proof(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "order_latency_ms": self.order_latency_ms,
            "feature_latency_ms": self.feature_latency_ms,
            "latency_artifact_id": self.latency_artifact_id,
            "missingness_state": self.missingness_state,
            "reasons": list(self.reasons),
            "decision_timestamp_ns": self.decision_timestamp_ns,
        }


def enrich_context_snapshot(
    snapshot: Mapping[str, Any],
    *,
    source_timestamp_ns: int,
) -> dict[str, Any]:
    out = {k: v for k, v in snapshot.items() if not str(k).startswith("_")}
    out[PROVENANCE_SOURCE_TS] = int(source_timestamp_ns)
    out["source_timestamp_ns"] = int(source_timestamp_ns)
    return out


def validate_macro_context(
    snapshot: Mapping[str, Any] | None,
    *,
    target_event_id: str | None = None,
    decision_timestamp_ns: int | None = None,
) -> MacroContextValidation:
    """Macro uplift is separate from target event; require declared event_context."""
    if not snapshot:
        return MacroContextValidation(
            ok=False,
            event_context=None,
            target_event_id=target_event_id,
            missingness_state="missing",
            reasons=["macro_context_snapshot_missing"],
            decision_timestamp_ns=decision_timestamp_ns,
        )

    reasons: list[str] = []
    event_ctx = snapshot.get("event_context")
    ctx_text = str(event_ctx).strip() if event_ctx is not None else ""
    src_ts = _source_ts(snapshot)
    if decision_timestamp_ns is not None:
        if src_ts is None:
            reasons.append("missing_macro_provenance")
        elif src_ts > decision_timestamp_ns:
            reasons.append("future_macro_source_timestamp")

    if not ctx_text or ctx_text.upper() == "NORMAL":
        missingness = "missing"
        ok = False
        if not ctx_text:
            reasons.append("event_context_missing")
        else:
            reasons.append("macro_context_not_active")
    else:
        missingness = "present"
        ok = not any(
            r in {"missing_macro_provenance", "future_macro_source_timestamp"}
            for r in reasons
        )

    return MacroContextValidation(
        ok=ok,
        event_context=ctx_text or None,
        target_event_id=target_event_id or snapshot.get("target_event_id"),
        missingness_state=missingness,
        reasons=reasons,
        source_timestamp_ns=src_ts,
        decision_timestamp_ns=decision_timestamp_ns,
    )


def validate_continuous_session(
    snapshot: Mapping[str, Any] | None,
    *,
    research_clock: str,
    decision_timestamp_ns: int | None = None,
) -> ContinuousSessionValidation:
    clock = str(research_clock or "scheduled_event").strip().lower()
    in_scope = clock == CONTINUOUS_CLOCK
    if not in_scope:
        return ContinuousSessionValidation(
            ok=False,
            in_scope=False,
            missingness_state="missing",
            reasons=["continuous_intraday_clock_out_of_scope"],
            research_clock=clock,
            decision_timestamp_ns=decision_timestamp_ns,
        )

    if not snapshot:
        return ContinuousSessionValidation(
            ok=False,
            in_scope=True,
            missingness_state="missing",
            reasons=["session_snapshot_missing"],
            research_clock=clock,
            decision_timestamp_ns=decision_timestamp_ns,
        )

    session_feats = snapshot.get("session_features")
    if not isinstance(session_feats, Mapping):
        session_feats = {
            k: snapshot.get(k)
            for k in SESSION_FEATURE_KEYS
            if k in snapshot
        }

    present: list[str] = []
    malformed: list[str] = []
    for key in sorted(SESSION_FEATURE_KEYS):
        state = _value_state(session_feats.get(key))
        if state == "present":
            present.append(key)
        elif state == "malformed":
            malformed.append(key)

    reasons: list[str] = []
    if malformed:
        reasons.extend(f"malformed_session:{k}" for k in malformed)
    src_ts = _source_ts(snapshot)
    if decision_timestamp_ns is not None:
        if src_ts is None:
            reasons.append("missing_session_provenance")
        elif src_ts > decision_timestamp_ns:
            reasons.append("future_session_source_timestamp")

    pit_blocked = any(
        r in {"missing_session_provenance", "future_session_source_timestamp"}
        for r in reasons
    )
    session_malformed = bool(malformed)
    ok = bool(present) and not session_malformed and not pit_blocked

    if not present:
        missingness = "missing"
    elif malformed:
        missingness = "malformed"
    elif present and len(present) < len(SESSION_FEATURE_KEYS):
        missingness = "available_not_selected"
    else:
        missingness = "present"

    return ContinuousSessionValidation(
        ok=ok,
        in_scope=True,
        missingness_state=missingness,
        session_features_present=present,
        reasons=reasons,
        research_clock=clock,
        decision_timestamp_ns=decision_timestamp_ns,
    )


def validate_latency_state(
    snapshot: Mapping[str, Any] | None,
    *,
    decision_timestamp_ns: int | None = None,
) -> LatencyStateValidation:
    if not snapshot:
        return LatencyStateValidation(
            ok=False,
            order_latency_ms=None,
            feature_latency_ms=None,
            latency_artifact_id=None,
            missingness_state="missing",
            reasons=["latency_snapshot_missing"],
            decision_timestamp_ns=decision_timestamp_ns,
        )

    reasons: list[str] = []
    order_raw = snapshot.get("order_latency_ms")
    feature_raw = snapshot.get("feature_latency_ms")
    artifact_id = snapshot.get("latency_artifact_id")

    order: float | None
    feature: float | None
    try:
        order = float(order_raw) if order_raw is not None else None
    except (TypeError, ValueError):
        order = None
        reasons.append("malformed_order_latency_ms")
    try:
        feature = float(feature_raw) if feature_raw is not None else None
    except (TypeError, ValueError):
        feature = None
        reasons.append("malformed_feature_latency_ms")

    if order is None:
        reasons.append("missing_order_latency_ms")
    elif order < 0:
        reasons.append("negative_order_latency_ms")
    if feature is None:
        reasons.append("missing_feature_latency_ms")
    elif feature < 0:
        reasons.append("negative_feature_latency_ms")

    ok = (
        order is not None
        and feature is not None
        and order >= 0
        and feature >= 0
        and not any(r.startswith("malformed_") for r in reasons)
    )
    missingness = "present" if ok else ("malformed" if any("malformed" in r for r in reasons) else "missing")

    return LatencyStateValidation(
        ok=ok,
        order_latency_ms=order,
        feature_latency_ms=feature,
        latency_artifact_id=str(artifact_id) if artifact_id else None,
        missingness_state=missingness,
        reasons=reasons,
        decision_timestamp_ns=decision_timestamp_ns,
    )


def apply_macro_to_recipe_family(
    family_row: MutableMapping[str, Any],
    validation: MacroContextValidation,
) -> None:
    family_row["allowed_context_events"] = [validation.event_context] if validation.event_context else []
    family_row["missingness_state"] = validation.missingness_state
    if validation.target_event_id:
        family_row["source_ids"] = [str(validation.target_event_id)]
    if validation.ok:
        family_row["model_consumption_state"] = "not_measured"
        family_row["pit_proof"] = "declared"
        family_row["why_not_used_or_sidelined"] = (
            "macro_context_declared_consumption_not_observed_in_screen"
        )
    else:
        family_row["model_consumption_state"] = "not_used" if validation.missingness_state == "missing" else "sidelined_missing_data"
        family_row["pit_proof"] = "rejected" if validation.reasons else "pending"
        family_row["why_not_used_or_sidelined"] = ";".join(validation.reasons) or "macro_context_unavailable"


def apply_continuous_session_to_recipe_family(
    family_row: MutableMapping[str, Any],
    validation: ContinuousSessionValidation,
) -> None:
    family_row["selected_features"] = list(validation.session_features_present)
    family_row["missingness_state"] = validation.missingness_state
    if not validation.in_scope:
        family_row["model_consumption_state"] = "sidelined_scope"
        family_row["pit_proof"] = "not_applicable"
        family_row["why_not_used_or_sidelined"] = "continuous_intraday_clock_out_of_scope_for_scheduled_screen"
        return
    if validation.ok:
        family_row["model_consumption_state"] = "not_measured"
        family_row["pit_proof"] = "declared"
        family_row["why_not_used_or_sidelined"] = (
            "continuous_session_features_present_consumption_not_observed_in_screen"
        )
    else:
        family_row["model_consumption_state"] = "sidelined_missing_data"
        family_row["pit_proof"] = "rejected" if validation.reasons else "pending"
        family_row["why_not_used_or_sidelined"] = ";".join(validation.reasons) or "continuous_session_unavailable"


def apply_latency_to_recipe_family(
    family_row: MutableMapping[str, Any],
    validation: LatencyStateValidation,
) -> None:
    family_row["missingness_state"] = validation.missingness_state
    if validation.latency_artifact_id:
        family_row["source_ids"] = [validation.latency_artifact_id]
    lag_feats: list[str] = []
    if validation.order_latency_ms is not None:
        lag_feats.append("order_latency_ms")
    if validation.feature_latency_ms is not None:
        lag_feats.append("feature_latency_ms")
    family_row["selected_features"] = lag_feats
    if validation.ok:
        family_row["model_consumption_state"] = "not_measured"
        family_row["pit_proof"] = "declared"
        family_row["why_not_used_or_sidelined"] = (
            "latency_state_declared_consumption_not_observed_in_screen"
        )
    else:
        family_row["model_consumption_state"] = "sidelined_missing_data"
        family_row["pit_proof"] = "rejected" if validation.reasons else "pending"
        family_row["why_not_used_or_sidelined"] = ";".join(validation.reasons) or "latency_state_unavailable"


def snapshot_from_market_state(
    state: Any,
    *,
    source_timestamp_ns: int,
    order_latency_ms: float,
    feature_latency_ms: float,
    latency_artifact_id: str | None = None,
    target_event_id: str | None = None,
) -> dict[str, Any]:
    """Build a context snapshot from a MarketState-like object at decision time."""
    event_context = getattr(state, "event_context", None)
    primary = getattr(state, "primary_features", None) or {}
    session_features = {
        key: primary.get(key)
        for key in SESSION_FEATURE_KEYS
        if key in primary and primary.get(key) is not None
    }
    base: dict[str, Any] = {
        "event_context": event_context,
        "target_event_id": target_event_id,
        "session_features": session_features,
        "order_latency_ms": order_latency_ms,
        "feature_latency_ms": feature_latency_ms,
    }
    if latency_artifact_id:
        base["latency_artifact_id"] = latency_artifact_id
    return enrich_context_snapshot(base, source_timestamp_ns=source_timestamp_ns)
