"""Natural-language hypothesis → structured ParsedHypothesis."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from features_engine.src.model_registry import (
    all_slugs,
    continuous_eligible_slugs,
    get_continuous_model_entry,
    legacy_to_slug,
    load_model_registry,
)

from research_pipeline.types import ContinuousLaneProfile, ParsedHypothesis

_PARSE_SYSTEM = """You convert natural-language trading hypotheses into JSON for a CME microstructure backtester.
Return ONLY JSON with keys:
instrument_universe (list of str),
entry_rules (list of str),
exit_rules (list of str),
indicators (list of str),
feature_list (list of str),
param_ranges (object mapping param name to [min, max]),
primary_model_id (str - must be one of the provided model slugs),
entry_rule (str, optional),
exit_rule (str, optional),
target_instruments (list of str, optional),
indicative_stop_loss (number, optional),
expected_holding_period (number, optional).
Do not invent new model ids."""

_SYMBOL_ALIASES_PATH = (
    Path(__file__).resolve().parents[1] / "features_engine" / "config" / "symbol_aliases.yaml"
)
_FALLBACK_PARAM_RANGES: Dict[str, List[float]] = {"signal_threshold": [0.05, 0.35]}

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


def _hypothesis_slugs() -> List[str]:
    reg = load_model_registry().get("models", {})
    return sorted(k for k, v in reg.items() if v.get("kind") == "hypothesis")


@lru_cache(maxsize=4)
def _symbol_aliases_for_path(path: str) -> Dict[str, List[str]]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    aliases: Dict[str, List[str]] = {}
    for canonical, values in data.items():
        items = [str(canonical)]
        if isinstance(values, list):
            items.extend(str(value) for value in values)
        aliases[str(canonical).upper()] = items
    return aliases


def _symbol_aliases() -> Dict[str, List[str]]:
    if not _SYMBOL_ALIASES_PATH.is_file():
        return {}
    return _symbol_aliases_for_path(str(_SYMBOL_ALIASES_PATH.resolve()))


def _clear_symbol_alias_cache() -> None:
    _symbol_aliases_for_path.cache_clear()


_symbol_aliases.cache_clear = _clear_symbol_alias_cache  # type: ignore[attr-defined]


def _normalize_alias_text(value: str) -> str:
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", value.upper()).split())


def _add_unique(values: List[str], item: str) -> None:
    if item and item not in values:
        values.append(item)


def _canonicalize_instrument(value: str) -> str:
    normalized = _normalize_alias_text(value)
    for canonical, aliases in _symbol_aliases().items():
        if any(_normalize_alias_text(alias) == normalized for alias in aliases):
            return canonical
    return str(value).upper()


def _instrument_universe_from_text(thesis: str, seed: Optional[List[str]] = None) -> List[str]:
    universe: List[str] = []
    for item in seed or []:
        _add_unique(universe, _canonicalize_instrument(str(item)))

    tokens = _normalize_alias_text(thesis).split()
    consumed: set[int] = set()
    alias_rows: List[tuple[int, str, List[str]]] = []
    for canonical, aliases in _symbol_aliases().items():
        for alias in aliases:
            alias_tokens = _normalize_alias_text(alias).split()
            if alias_tokens:
                alias_rows.append((len(alias_tokens), canonical, alias_tokens))

    for _, canonical, alias_tokens in sorted(alias_rows, reverse=True):
        width = len(alias_tokens)
        for start in range(0, len(tokens) - width + 1):
            idxs = set(range(start, start + width))
            if idxs & consumed:
                continue
            if tokens[start:start + width] == alias_tokens:
                _add_unique(universe, canonical)
                consumed.update(idxs)
                break
    if not universe:
        universe.append("MES")
    return universe


def _normalize_param_ranges(param_ranges: Any) -> Dict[str, List[float]]:
    normalized: Dict[str, List[float]] = {}
    if isinstance(param_ranges, dict):
        for k, v in param_ranges.items():
            if isinstance(v, (list, tuple)) and len(v) >= 2:
                normalized[str(k)] = [float(v[0]), float(v[1])]
    return normalized


def _default_param_ranges_for_model(model_id: str) -> Dict[str, List[float]]:
    entry = load_model_registry().get("models", {}).get(model_id, {})
    normalized = _normalize_param_ranges(entry.get("default_param_ranges"))
    return normalized or dict(_FALLBACK_PARAM_RANGES)


def _model_metadata(model_id: str) -> Dict[str, Any]:
    entry = load_model_registry().get("models", {}).get(model_id, {})
    keys = (
        "recommended_horizon_bars",
        "valid_instrument_universe",
        "target_instrument_universe",
        "context_instrument_universe",
        "volatility_regime",
        "risk_metrics",
        "feature_recipe",
    )
    return {key: entry[key] for key in keys if key in entry}


def _with_instrument_compatibility(
    metadata: Dict[str, Any],
    instrument_universe: List[str],
) -> Dict[str, Any]:
    metadata = dict(metadata)
    valid = [str(symbol).upper() for symbol in metadata.get("valid_instrument_universe") or []]
    if not valid:
        metadata["unsupported_instruments"] = list(instrument_universe)
        metadata["compatible_instrument_universe"] = []
        metadata["instrument_universe_compatibility"] = "missing_valid_instrument_universe"
        return metadata

    context_valid = [
        str(symbol).upper()
        for symbol in metadata.get("context_instrument_universe") or []
    ]
    unsupported = [symbol for symbol in instrument_universe if symbol.upper() not in valid]
    compatible = [symbol for symbol in instrument_universe if symbol.upper() in valid]
    context = [symbol for symbol in unsupported if symbol.upper() in context_valid]
    still_unsupported = [
        symbol for symbol in unsupported if symbol.upper() not in context_valid
    ]
    metadata["compatible_instrument_universe"] = compatible
    if context:
        metadata["context_instrument_universe"] = context
    if still_unsupported:
        metadata["unsupported_instruments"] = still_unsupported
        metadata["instrument_universe_compatibility"] = "unsupported_instruments"
        return metadata
    metadata["unsupported_instruments"] = []
    if compatible or context:
        metadata["instrument_universe_compatibility"] = "compatible"
        return metadata
    metadata["instrument_universe_compatibility"] = "compatible"
    return metadata


canonicalize_instrument = _canonicalize_instrument
model_metadata = _model_metadata
with_instrument_compatibility = _with_instrument_compatibility


def _slug_from_parentheses(thesis: str) -> Optional[str]:
    """Extract canonical slug from thesis template '(SLUG)' suffix."""
    models = load_model_registry().get("models", {})
    for match in re.finditer(r"\(([A-Z][A-Z0-9_]+)\)", thesis):
        slug = match.group(1)
        if slug in models and _is_hypothesis_model(slug):
            return slug
    return None


def _legacy_slug_from_thesis(thesis: str) -> Optional[str]:
    match = re.search(r"\bHYP_(\d+)\b", thesis, re.I)
    if not match:
        return None
    legacy = f"HYP_{match.group(1)}"
    return legacy_to_slug().get(legacy)


def _normalize_model_match_text(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def _is_hypothesis_model(slug: str) -> bool:
    entry = load_model_registry().get("models", {}).get(slug, {})
    return entry.get("kind") == "hypothesis"


def _contains_model_alias(haystack: str, alias: str) -> bool:
    normalized = _normalize_model_match_text(alias)
    if not normalized:
        return False
    return re.search(rf"\b{re.escape(normalized)}\b", haystack) is not None


def _match_model(thesis: str) -> str:
    slug_paren = _slug_from_parentheses(thesis)
    if slug_paren is not None:
        return slug_paren
    continuous_paren = _continuous_slug_from_parentheses(thesis)
    if continuous_paren is not None:
        raise ValueError(f"{continuous_paren} is not continuous-eligible for the event lane; use the continuous lane")
    legacy_slug = _legacy_slug_from_thesis(thesis)
    if legacy_slug is not None:
        return legacy_slug
    lower = _normalize_model_match_text(thesis)
    alias_matches: List[tuple[int, str]] = []
    for slug, entry in load_model_registry().get("models", {}).items():
        if entry.get("kind") != "hypothesis":
            continue
        candidates = [slug.replace("_", " "), str(entry.get("display_name") or "")]
        candidates.extend(str(alias) for alias in entry.get("aliases") or [])
        for candidate in candidates:
            normalized = _normalize_model_match_text(candidate)
            if normalized and _contains_model_alias(lower, normalized):
                alias_matches.append((len(normalized), slug))
    if alias_matches:
        return sorted(alias_matches, reverse=True)[0][1]
    for pattern, slug in _KEYWORD_MODEL:
        if re.search(pattern, lower) and _is_hypothesis_model(slug):
            return slug
    for slug, entry in load_model_registry().get("models", {}).items():
        if entry.get("kind") != "hypothesis":
            continue
        display = str(entry.get("display_name") or "")
        if display and _contains_model_alias(lower, display):
            return slug
    return "SPREAD_BLOWOUT_RECOMPRESSION"


def _heuristic_parse(thesis: str) -> ParsedHypothesis:
    model_id = _match_model(thesis)
    universe = _instrument_universe_from_text(thesis)
    metadata = _with_instrument_compatibility(_model_metadata(model_id), universe)
    return ParsedHypothesis(
        thesis=thesis,
        instrument_universe=universe,
        entry_rules=[f"Enter when {model_id} signal exceeds threshold"],
        exit_rules=["Exit on signal mean reversion or session end"],
        indicators=["microstructure_signal"],
        feature_list=[model_id],
        param_ranges=_default_param_ranges_for_model(model_id),
        primary_model_id=model_id,
        source="heuristic",
        metadata=metadata,
    )


def _parse_dict_common(thesis: str, data: Dict[str, Any], source: str) -> ParsedHypothesis:
    slugs = set(_hypothesis_slugs())
    model_id = str(data.get("primary_model_id", ""))
    if model_id not in slugs:
        model_id = _match_model(thesis)
    normalized = _normalize_param_ranges(data.get("param_ranges"))
    if not normalized:
        normalized = _default_param_ranges_for_model(model_id)

    entry_rules = list(data.get("entry_rules") or [])
    exit_rules = list(data.get("exit_rules") or [])
    if data.get("entry_rule"):
        entry_rules.append(str(data["entry_rule"]))
    if data.get("exit_rule"):
        exit_rules.append(str(data["exit_rule"]))

    seed_universe: List[str] = []
    for key in ("instrument_universe", "target_instruments"):
        value = data.get(key)
        if isinstance(value, list):
            seed_universe.extend(str(item) for item in value)
        elif value:
            seed_universe.append(str(value))
    metadata = _model_metadata(model_id)
    for key in ("indicative_stop_loss", "expected_holding_period", "entry_rule", "exit_rule"):
        if key in data:
            metadata[key] = data[key]
    instrument_universe = _instrument_universe_from_text(thesis, [str(item) for item in seed_universe])
    metadata = _with_instrument_compatibility(metadata, instrument_universe)
    return ParsedHypothesis(
        thesis=thesis,
        instrument_universe=instrument_universe,
        entry_rules=entry_rules,
        exit_rules=exit_rules,
        indicators=list(data.get("indicators") or []),
        feature_list=list(data.get("feature_list") or [model_id]),
        param_ranges=normalized,
        primary_model_id=model_id,
        source=source,
        metadata=metadata,
    )


def _from_llm_dict(thesis: str, data: Dict[str, Any]) -> ParsedHypothesis:
    return _parse_dict_common(thesis, data, "openai_compatible")


def _from_hypothesis_packet(thesis: str, data: Dict[str, Any]) -> ParsedHypothesis:
    return _parse_dict_common(thesis, data, "hypothesis_packet")


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
    from research_pipeline.llm import generate_json

    slugs = _hypothesis_slugs()
    user = f"Thesis:\n{thesis}\n\nAllowed primary_model_id values:\n" + ", ".join(slugs)
    data, err = generate_json(_PARSE_SYSTEM, user)
    if data is None:
        parsed = _heuristic_parse(thesis)
        parsed.source = "heuristic"
        return parsed
    return _from_llm_dict(thesis, data)


# ---------------------------------------------------------------------------
# Continuous microstructure lane parsing (Phase 4/5). Additive — event-lane
# functions above are unchanged.
# ---------------------------------------------------------------------------

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

_FAMILY_THESIS_PATTERNS: dict[str, list[str]] = {
    "micro_standard": [r"\bmicro\b", r"\bMES\b", r"\bMNQ\b", r"\bMGC\b", r"\bMCL\b", r"\bES\b", r"\bNQ\b", r"lead.?lag", r"flow transfer"],
    "metals_complex": [r"\bGC\b", r"\bSI\b", r"\bHG\b", r"gold", r"silver", r"metal"],
    "energy_complex": [r"\bCL\b", r"\bRB\b", r"\bHO\b", r"\bNG\b", r"\bMCL\b", r"crude", r"energy", r"natgas"],
    "rates_curve": [r"\bZT\b", r"\bZF\b", r"\bZN\b", r"\bZB\b", r"\bUB\b", r"\brates\b", r"\btreasury\b", r"\byield\s+curve\b", r"\btreasury\s+curve\b"],
    "calendar_front_second": [r"calendar", r"front.?second", r"term structure", r"roll"],
    "seasonal_state": [r"seasonal", r"seasonality", r"day.?of.?week", r"month.?of.?year"],
}


def _continuous_slugs() -> List[str]:
    return continuous_eligible_slugs()


def _continuous_slug_set() -> set[str]:
    return set(_continuous_slugs())


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
        thesis, [str(t) for t in types], relationship_graph=relationship_graph,
    )


def _continuous_slug_from_parentheses(thesis: str) -> Optional[str]:
    """Extract a continuous-eligible slug from a '(SLUG)' suffix."""
    models = load_model_registry().get("models", {})
    for match in re.finditer(r"\(([A-Z][A-Z0-9_]+)\)", thesis):
        slug = match.group(1)
        if slug in models and slug in _continuous_slug_set():
            return slug
    return None


def _match_continuous_model(thesis: str) -> str:
    slug_paren = _continuous_slug_from_parentheses(thesis)
    if slug_paren is not None:
        return slug_paren
    event_slug_paren = _slug_from_parentheses(thesis)
    if event_slug_paren is not None:
        raise ValueError(f"{event_slug_paren} is not continuous-eligible")
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


def _normalize_continuous_param_ranges(raw: Any) -> Dict[str, List[float]]:
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
    param_ranges = _normalize_continuous_param_ranges(entry.get("default_param_ranges"))
    if not param_ranges:
        param_ranges = {"signal_threshold": [0.05, 0.35]}
    return ContinuousLaneProfile(
        thesis=thesis,
        lane="continuous_microstructure",
        primary_model_id=model_id,
        model_family=str(entry.get("model_family") or "unknown"),
        universe_profile=universe_profile,
        relationship_family=_relationship_family_from_entry(
            entry, thesis=thesis, relationship_graph=relationship_graph,
        ),
        param_ranges=param_ranges,
        source="heuristic",
    )
