"""Natural-language hypothesis → structured ParsedHypothesis."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from features_engine.src.model_registry import (
    all_slugs,
    continuous_eligible_slugs,
    get_continuous_model_entry,
    legacy_to_slug,
    load_model_registry,
)

from research_pipeline.llm import generate_json
from research_pipeline.types import ContinuousLaneProfile, ParsedHypothesis

_PARSE_SYSTEM = """You convert natural-language trading hypotheses into JSON for a CME microstructure backtester.
Return ONLY JSON with keys:
instrument_universe (list of str),
entry_rules (list of str),
exit_rules (list of str),
indicators (list of str),
feature_list (list of str),
param_ranges (object mapping param name to [min, max]),
primary_model_id (str — must be one of the provided model slugs).
Do not invent new model ids."""

_KEYWORD_MODEL: List[tuple[str, str]] = [
    (r"spread", "SPREAD_BLOWOUT_RECOMPRESSION"),
    (r"book pressure|ofi|mlofi", "BOOK_PRESSURE"),
    (r"vpin|toxic", "VPIN_TOXICITY"),
    (r"second wave|continuation", "SECOND_WAVE_CONTINUATION"),
    (r"stop.?run|exhaustion", "STOP_RUN_EXHAUSTION_FADE"),
    (r"liquidity vacuum", "LIQUIDITY_VACUUM_CONTINUATION"),
    (r"depth.?refill|imbalance", "DEPTH_REFILL_IMBALANCE"),
    (r"false breakout|trap", "FALSE_BREAKOUT_TRAP"),
    (r"cancel storm", "CANCEL_STORM_BEFORE_MOVE"),
    (r"queue depletion", "QUEUE_DEPLETION_TRIGGER"),
    (r"absorption", "ABSORPTION_FADE"),
    (r"iceberg|reload", "ICEBERG_RELOAD_DETECTION"),
    (r"lead.?lag|transfer entropy", "TRANSFER_ENTROPY"),
    (r"hybrid|avellaneda", "HYBRID_EXECUTION"),
]

_CONTINUOUS_KEYWORD_MODEL: List[tuple[str, str]] = [
    (r"cross.?market ofi|ofi impact", "CROSS_MARKET_OFI_IMPACT"),
    (r"book resiliency|resiliency continuation", "BOOK_RESILIENCY_CONTINUATION"),
    (r"queue depletion|replenishment", "QUEUE_DEPLETION_REPLENISHMENT"),
    (r"hidden liquidity|iceberg reload", "HIDDEN_LIQUIDITY_RELOAD"),
    (r"toxic flow|adverse selection", "TOXIC_FLOW_ADVERSE_SELECTION"),
    (r"calendar curve|term structure impulse", "CALENDAR_CURVE_MICRO_IMPULSE"),
    (r"spread dislocation|relative value spread", "STRUCTURAL_SPREAD_MICRO_DISLOCATION"),
    (r"seasonal state|seasonality", "SEASONAL_STATE_CONDITIONED_MICRO_ALPHA"),
    (r"self.?exciting|hawkes|flow burst", "SELF_EXCITING_FLOW_BURST"),
    (r"rl execution|execution overlay", "RL_EXECUTION_OVERLAY"),
    (r"micro.?standard|flow transfer", "MICRO_STANDARD_FLOW_TRANSFER"),
    (r"lead.?lag", "MICRO_STANDARD_FLOW_TRANSFER"),
]


def _continuous_slugs() -> List[str]:
    return continuous_eligible_slugs()


def _continuous_slug_set() -> set[str]:
    return set(_continuous_slugs())


def _event_lane_slug_set() -> set[str]:
    """Event-lane slugs: registry entries excluding continuous_microstructure."""
    return {slug for slug in all_slugs() if slug not in _continuous_slug_set()}


_FAMILY_THESIS_PATTERNS: dict[str, list[str]] = {
    "micro_standard": [
        r"\bmicro\b",
        r"\bMES\b",
        r"\bMNQ\b",
        r"\bMGC\b",
        r"\bMCL\b",
        r"\bES\b",
        r"\bNQ\b",
        r"lead.?lag",
        r"flow transfer",
    ],
    "metals_complex": [
        r"\bGC\b",
        r"\bSI\b",
        r"\bHG\b",
        r"gold",
        r"silver",
        r"metal",
    ],
    "energy_complex": [
        r"\bCL\b",
        r"\bRB\b",
        r"\bHO\b",
        r"\bNG\b",
        r"\bMCL\b",
        r"crude",
        r"energy",
        r"natgas",
    ],
    "rates_curve": [
        r"\bZT\b",
        r"\bZF\b",
        r"\bZN\b",
        r"\bZB\b",
        r"\bUB\b",
        r"\brates\b",
        r"\btreasury\b",
        r"\byield\s+curve\b",
        r"\btreasury\s+curve\b",
    ],
    "calendar_front_second": [
        r"calendar",
        r"front.?second",
        r"term structure",
        r"roll",
    ],
    "seasonal_state": [
        r"seasonal",
        r"seasonality",
        r"day.?of.?week",
        r"month.?of.?year",
    ],
}


def _graph_active_families(graph: Optional[dict[str, Any]]) -> set[str]:
    if not graph:
        return set()
    active: set[str] = set()
    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        family_id = edge.get("family_id")
        if isinstance(family_id, str) and family_id.strip():
            active.add(family_id.strip())
    for family_id in graph.get("families") or []:
        if isinstance(family_id, str) and family_id.strip():
            active.add(family_id.strip())
    return active


def _score_relationship_family(thesis: str, family_id: str) -> int:
    score = 0
    for pattern in _FAMILY_THESIS_PATTERNS.get(family_id, []):
        if re.search(pattern, thesis, re.I):
            score += 1
    return score


def disambiguate_relationship_family(
    thesis: str,
    valid_types: list[str],
    *,
    relationship_graph: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Pick one relationship family when registry lists multiple (Phase 5)."""
    if not valid_types:
        return None
    if len(valid_types) == 1:
        return str(valid_types[0])

    candidates = [str(t) for t in valid_types]
    scores = {family_id: _score_relationship_family(thesis, family_id) for family_id in candidates}
    active = _graph_active_families(relationship_graph)
    if active and all(family_id in active for family_id in candidates):
        for family_id in candidates:
            scores[family_id] += 1

    best_score = max(scores.values())
    if best_score <= 0:
        return None
    winners = [family_id for family_id, score in scores.items() if score == best_score]
    if len(winners) != 1:
        return None
    return winners[0]


def _relationship_family_from_entry(
    entry: dict,
    *,
    thesis: str = "",
    relationship_graph: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    types = entry.get("valid_relationship_types") or []
    if not types:
        return None
    if len(types) == 1:
        return str(types[0])
    return disambiguate_relationship_family(
        thesis,
        [str(t) for t in types],
        relationship_graph=relationship_graph,
    )


def _slug_from_parentheses(thesis: str) -> Optional[str]:
    """Extract canonical slug from thesis template '(SLUG)' suffix."""
    models = load_model_registry().get("models", {})
    for match in re.finditer(r"\(([A-Z][A-Z0-9_]+)\)", thesis):
        slug = match.group(1)
        if slug in models:
            return slug
    return None


def _continuous_slug_from_parentheses(thesis: str) -> Optional[str]:
    slug = _slug_from_parentheses(thesis)
    if slug is None:
        return None
    if slug not in _continuous_slug_set():
        raise ValueError(f"{slug} is not continuous-eligible")
    return slug


def _match_continuous_model(thesis: str) -> str:
    slug_paren = _continuous_slug_from_parentheses(thesis)
    if slug_paren is not None:
        return slug_paren
    lower = thesis.lower()
    for pattern, slug in _CONTINUOUS_KEYWORD_MODEL:
        if re.search(pattern, lower):
            return slug
    for slug in _continuous_slugs():
        entry = get_continuous_model_entry(slug)
        display = str(entry.get("display_name") or "").lower()
        if display and display in lower:
            return slug
    raise ValueError(
        "cannot infer continuous model from thesis; include (CONTINUOUS_SLUG) or recognizable keywords"
    )


def _normalize_param_ranges(raw: Any) -> Dict[str, List[float]]:
    if not isinstance(raw, dict):
        return {}
    normalized: Dict[str, List[float]] = {}
    for key, value in raw.items():
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            normalized[str(key)] = [float(value[0]), float(value[1])]
    return normalized


def parse_continuous_lane_profile(
    thesis: str,
    *,
    universe_profile: str = "full_cme_research",
    use_llm: bool = False,
    relationship_graph: Optional[dict[str, Any]] = None,
) -> ContinuousLaneProfile:
    """Parse continuous microstructure lane profile without universe expansion."""
    thesis = thesis.strip()
    if not thesis:
        raise ValueError("thesis must be non-empty")
    if use_llm:
        raise NotImplementedError("continuous lane LLM parse deferred to Phase 5")

    model_id = _match_continuous_model(thesis)
    entry = get_continuous_model_entry(model_id)
    param_ranges = _normalize_param_ranges(entry.get("default_param_ranges"))
    if not param_ranges:
        param_ranges = {"signal_threshold": [0.05, 0.35]}

    return ContinuousLaneProfile(
        thesis=thesis,
        lane="continuous_microstructure",
        primary_model_id=model_id,
        model_family=str(entry.get("model_family") or "unknown"),
        universe_profile=universe_profile,
        relationship_family=_relationship_family_from_entry(
            entry,
            thesis=thesis,
            relationship_graph=relationship_graph,
        ),
        param_ranges=param_ranges,
        source="heuristic",
    )


def _hypothesis_slugs() -> List[str]:
    reg = load_model_registry().get("models", {})
    return sorted(k for k, v in reg.items() if v.get("kind") == "hypothesis")


def _legacy_slug_from_thesis(thesis: str) -> Optional[str]:
    match = re.search(r"\bHYP_(\d+)\b", thesis, re.I)
    if not match:
        return None
    legacy = f"HYP_{match.group(1)}"
    return legacy_to_slug().get(legacy)


def _match_model(thesis: str) -> str:
    slug_paren = _slug_from_parentheses(thesis)
    if slug_paren is not None:
        if slug_paren in _continuous_slug_set():
            raise ValueError(
                f"{slug_paren} is continuous-eligible; use parse_continuous_lane_profile"
            )
        return slug_paren
    legacy_slug = _legacy_slug_from_thesis(thesis)
    if legacy_slug is not None:
        return legacy_slug
    lower = thesis.lower()
    for pattern, slug in _KEYWORD_MODEL:
        if re.search(pattern, lower):
            return slug
    for slug, entry in load_model_registry().get("models", {}).items():
        if entry.get("kind") != "hypothesis":
            continue
        display = str(entry.get("display_name") or "").lower()
        if display and display in lower:
            return slug
    return "SPREAD_BLOWOUT_RECOMPRESSION"


def _heuristic_parse(thesis: str) -> ParsedHypothesis:
    model_id = _match_model(thesis)
    universe = ["MES"]
    if re.search(r"\bNQ\b", thesis, re.I):
        universe.append("NQ")
    if re.search(r"\bES\b", thesis, re.I):
        universe.append("ES")
    return ParsedHypothesis(
        thesis=thesis,
        instrument_universe=universe,
        entry_rules=[f"Enter when {model_id} signal exceeds threshold"],
        exit_rules=["Exit on signal mean reversion or session end"],
        indicators=["microstructure_signal"],
        feature_list=[model_id],
        param_ranges={"signal_threshold": [0.05, 0.35]},
        primary_model_id=model_id,
        source="heuristic",
    )


def _parse_dict_common(thesis: str, data: Dict[str, Any], source: str) -> ParsedHypothesis:
    slugs = _event_lane_slug_set()
    model_id = str(data.get("primary_model_id", ""))
    if model_id not in slugs:
        model_id = _match_model(thesis)
    param_ranges = data.get("param_ranges") or {"signal_threshold": [0.05, 0.35]}
    normalized: Dict[str, List[float]] = {}
    for k, v in param_ranges.items():
        if isinstance(v, (list, tuple)) and len(v) >= 2:
            normalized[str(k)] = [float(v[0]), float(v[1])]
    if not normalized:
        normalized = {"signal_threshold": [0.05, 0.35]}
    return ParsedHypothesis(
        thesis=thesis,
        instrument_universe=list(data.get("instrument_universe") or ["MES"]),
        entry_rules=list(data.get("entry_rules") or []),
        exit_rules=list(data.get("exit_rules") or []),
        indicators=list(data.get("indicators") or []),
        feature_list=list(data.get("feature_list") or [model_id]),
        param_ranges=normalized,
        primary_model_id=model_id,
        source=source,
    )


def _from_llm_dict(thesis: str, data: Dict[str, Any]) -> ParsedHypothesis:
    return _parse_dict_common(thesis, data, "openai_compatible")


def _from_hypothesis_packet(thesis: str, data: Dict[str, Any]) -> ParsedHypothesis:
    return _parse_dict_common(thesis, data, "openai_compatible")


def parse_hypothesis(
    thesis: str,
    *,
    use_llm: bool = True,
    pipeline_request: Optional[Dict[str, Any]] = None,
    repo_root: Optional[Path] = None,
) -> ParsedHypothesis:
    thesis = thesis.strip()
    if not thesis:
        raise ValueError("thesis must be non-empty")
    if not use_llm:
        parsed = _heuristic_parse(thesis)
        parsed.llm_status = "skipped_no_llm"
        return parsed
    if pipeline_request is not None and repo_root is not None:
        from data_layer.llm.packet_runner import run_llm_on_hypothesis_request

        data = run_llm_on_hypothesis_request(
            pipeline_request,
            thesis,
            allowed_model_ids=_hypothesis_slugs(),
            repo_root=repo_root,
        )
        if data.get("llm_status") == "ok":
            parsed = _from_hypothesis_packet(thesis, data)
            parsed.llm_status = "ok"
            return parsed
        parsed = _heuristic_parse(thesis)
        parsed.source = "heuristic"
        parsed.llm_status = str(data.get("llm_status") or "unavailable")
        return parsed
    slugs = _hypothesis_slugs()
    user = f"Thesis:\n{thesis}\n\nAllowed primary_model_id values:\n" + ", ".join(slugs)
    data, err = generate_json(_PARSE_SYSTEM, user)
    if data is None:
        parsed = _heuristic_parse(thesis)
        parsed.source = "heuristic"
        return parsed
    return _from_llm_dict(thesis, data)
