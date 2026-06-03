"""Lane-aware promotion coverage check.

Extends the CME-only _scorecard_covers in promotion_gate.py with a
lane-aware version that consults the LaneRegistry to determine the
candidate's lane and checks coverage within that lane.

Cross-lane promotion (e.g. crypto symbol against CME coverage) is
rejected. The legacy CME check is preserved as a fallback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .lane import Lane
from .lane_registry import LaneRegistry
from .registration import register_all_lanes
from .scorecard import build_lane_scorecard

CRYPTO_VALIDATED_DATA_ENVIRONMENT = "btc_computer_validated_crypto_data_environment"
CRYPTO_ENVIRONMENT_COVERAGE = "validated_crypto_data_environment"
CRYPTO_ENVIRONMENT_SOURCE_PREFIXES = (
    "config://btc-computer/",
    "artifact://btc-computer/",
    "runtime://btc-computer/",
    "data_env://btc-computer/",
)


@dataclass
class LaneCoverageResult:
    """Result of a lane-aware coverage check."""

    lane: str
    passed: bool
    symbol: str
    event_id: str
    queue_model: str = ""
    failure_reasons: list[str] = field(default_factory=list)
    matched_lane: str | None = None
    capability_profile: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane,
            "passed": self.passed,
            "symbol": self.symbol,
            "event_id": self.event_id,
            "queue_model": self.queue_model,
            "failure_reasons": list(self.failure_reasons),
            "matched_lane": self.matched_lane,
            "capability_profile": dict(self.capability_profile),
        }


def resolve_lane_for_candidate(
    model_id: str = "",
    symbol: str = "",
    event_id: str = "",
    *,
    auto_register: bool = True,
) -> Lane:
    """Resolve the candidate's lane from model_id, symbol, or event_id prefix.

    Priority: model_id > non-crypto symbol prefix > event_id prefix > CME_FUTURES default.
    Crypto is intentionally not inferred from ticker names; the crypto lane
    must come from a CRYPTO_ model/event id or upstream provenance.
    """
    if auto_register:
        register_all_lanes()
    reg = LaneRegistry.instance()
    if model_id:
        lane = reg.resolve_lane(model_id)
        if lane != Lane.CME_FUTURES:
            return lane
    sym_upper = (symbol or "").upper()
    if sym_upper.startswith(("RUNNER", "LOW_FLOAT")):
        return Lane.EQUITIES
    if sym_upper.startswith(("OPTIONS", "PARITY")):
        return Lane.OPTIONS
    eid_upper = (event_id or "").upper()
    if eid_upper.startswith("CRYPTO_"):
        return Lane.CRYPTO
    if eid_upper.startswith(("EQUITY_", "LOW_FLOAT_")):
        return Lane.EQUITIES
    if eid_upper.startswith(("OPTIONS_", "PARITY_")):
        return Lane.OPTIONS
    return Lane.CME_FUTURES


def _symbol_matches(symbol: str, coverage_symbols: list[str]) -> bool:
    """Check if a symbol matches any in the coverage list. Supports exact, prefix, and substring match."""
    if not symbol:
        return True
    sym_upper = symbol.upper()
    for cs in coverage_symbols:
        cs_upper = cs.upper()
        if sym_upper == cs_upper:
            return True
        if sym_upper.startswith(cs_upper) or cs_upper.startswith(sym_upper):
            return True
    return False


def _crypto_symbol_matches_environment(symbol: str, lane_dict: dict[str, Any]) -> bool:
    """Crypto symbols are covered by config/provenance, not static ticker prefixes."""
    if not symbol:
        return True
    symbols = lane_dict.get("symbols", [])
    if symbols:
        return _symbol_matches(symbol, symbols)
    source_ref = str(lane_dict.get("environment_source_ref", ""))
    return (
        lane_dict.get("instrument_coverage") == CRYPTO_ENVIRONMENT_COVERAGE
        and lane_dict.get("data_environment") == CRYPTO_VALIDATED_DATA_ENVIRONMENT
        and bool(lane_dict.get("environment_validated"))
        and source_ref.startswith(CRYPTO_ENVIRONMENT_SOURCE_PREFIXES)
    )


def _capability_failures(lane: Lane, lane_dict: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    profile = lane_dict.get("capability_profile", {})
    if not isinstance(profile, dict):
        return [f"lane '{lane.value}' capability_profile is invalid"], {}
    failures: list[str] = []
    if lane == Lane.CME_FUTURES:
        if not profile.get("is_hft") or not profile.get("dma") or not profile.get("hft_proof_required"):
            failures.append("CME lane requires true HFT/DMA capability proof")
    elif lane == Lane.CRYPTO:
        if not profile.get("is_hft") or not profile.get("node_direct") or not profile.get("hft_proof_required"):
            failures.append("crypto lane requires node-direct HFT capability proof")
    elif lane == Lane.EQUITIES:
        if not profile.get("speed_advantage") or profile.get("dma"):
            failures.append("equities lane requires speed-advantage non-DMA capability")
    elif lane == Lane.OPTIONS:
        if not profile.get("research_only") or profile.get("is_hft"):
            failures.append("options lane is research/non-HFT unless separately proven")
    return failures, profile


def _event_type_matches(event_id: str, coverage_event_types: list[str]) -> bool:
    """Check if an event_id matches any coverage event type. Supports wildcard match."""
    if not event_id or not coverage_event_types:
        return True
    eid_lower = event_id.lower()
    prefix = eid_lower.split("_")[0]
    eid_lower_full = eid_lower
    for et in coverage_event_types:
        et_lower = et.lower()
        if et_lower in ("macro", "synthetic"):
            if prefix in ("cpi", "nfp", "ppi", "fomc", "gdp", "ism") or "macro" in eid_lower_full:
                return True
            if et_lower == "macro":
                continue
        if prefix == et_lower:
            return True
        if eid_lower_full.startswith(et_lower):
            return True
    return False


def check_lane_coverage(
    lane: Lane,
    *,
    symbol: str = "",
    event_id: str = "",
    latency_ms: float = 0.0,
    queue_model: str = "",
    scorecard: dict[str, Any] | None = None,
    auto_register: bool = True,
) -> LaneCoverageResult:
    """Check whether a candidate's symbol/event/latency are covered by the given lane.

    scorecard: optional pre-built scorecard dict; if None, builds one.
    """
    if auto_register:
        register_all_lanes()
    if scorecard is None:
        card = build_lane_scorecard()
        scorecard = card.to_dict()
    lane_coverage = scorecard.get("lane_coverage", {})
    lane_dict = lane_coverage.get(lane.value, {})
    failures: list[str] = []
    symbols = lane_dict.get("symbols", [])
    event_types = lane_dict.get("event_types", [])
    latency_bands = lane_dict.get("latency_bands_ms", [])
    queue_models = lane_dict.get("queue_models", [])
    capability_failures, capability_profile = _capability_failures(lane, lane_dict)
    failures.extend(capability_failures)
    if symbol:
        if lane == Lane.CRYPTO:
            if not _crypto_symbol_matches_environment(symbol, lane_dict):
                failures.append(
                    f"symbol '{symbol}' not covered by crypto data environment/config provenance"
                )
        elif not _symbol_matches(symbol, symbols):
            failures.append(
                f"symbol '{symbol}' not in lane '{lane.value}' coverage symbols {symbols}"
            )
    if event_id and not _event_type_matches(event_id, event_types):
        failures.append(
            f"event_id '{event_id}' does not match lane '{lane.value}' event_types {event_types}"
        )
    if latency_ms and latency_bands:
        if not any(abs(latency_ms - band) < 0.01 for band in latency_bands):
            nearest = min(latency_bands, key=lambda b: abs(b - latency_ms))
            failures.append(
                f"latency_ms {latency_ms} not in lane '{lane.value}' latency_bands (nearest={nearest})"
            )
    if queue_model and queue_models and queue_model not in queue_models:
        failures.append(
            f"queue_model '{queue_model}' not in lane '{lane.value}' queue_models {queue_models}"
        )
    return LaneCoverageResult(
        lane=lane.value,
        passed=not failures,
        symbol=symbol,
        event_id=event_id,
        queue_model=queue_model,
        failure_reasons=failures,
        matched_lane=lane.value if not failures else None,
        capability_profile=capability_profile,
    )


def check_candidate_lane_coverage(
    *,
    model_id: str = "",
    symbol: str = "",
    event_id: str = "",
    latency_ms: float = 0.0,
    queue_model: str = "",
    scorecard: dict[str, Any] | None = None,
    auto_register: bool = True,
) -> LaneCoverageResult:
    """End-to-end: resolve the candidate's lane and check coverage.

    Returns a LaneCoverageResult. If the lane has no coverage, returns
    a passed=False result with a clear reason.
    """
    if auto_register:
        register_all_lanes()
    lane = resolve_lane_for_candidate(model_id=model_id, symbol=symbol, event_id=event_id)
    return check_lane_coverage(
        lane,
        symbol=symbol,
        event_id=event_id,
        latency_ms=latency_ms,
        queue_model=queue_model,
        scorecard=scorecard,
        auto_register=False,
    )
