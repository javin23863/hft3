"""Natural-language hypothesis → structured ParsedHypothesis."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from features_engine.src.model_registry import all_slugs, legacy_to_slug, load_model_registry

from research_pipeline.types import ParsedHypothesis

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

    unsupported = [symbol for symbol in instrument_universe if symbol.upper() not in valid]
    metadata["unsupported_instruments"] = unsupported
    metadata["compatible_instrument_universe"] = [
        symbol for symbol in instrument_universe if symbol.upper() in valid
    ]
    metadata["instrument_universe_compatibility"] = (
        "unsupported_instruments" if unsupported else "compatible"
    )
    return metadata


canonicalize_instrument = _canonicalize_instrument
model_metadata = _model_metadata
with_instrument_compatibility = _with_instrument_compatibility


def _slug_from_parentheses(thesis: str) -> Optional[str]:
    """Extract canonical slug from thesis template '(SLUG)' suffix."""
    models = load_model_registry().get("models", {})
    for match in re.finditer(r"\(([A-Z][A-Z0-9_]+)\)", thesis):
        slug = match.group(1)
        if slug in models:
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


def _contains_model_alias(haystack: str, alias: str) -> bool:
    normalized = _normalize_model_match_text(alias)
    if not normalized:
        return False
    return re.search(rf"\b{re.escape(normalized)}\b", haystack) is not None


def _match_model(thesis: str) -> str:
    slug_paren = _slug_from_parentheses(thesis)
    if slug_paren is not None:
        return slug_paren
    legacy_slug = _legacy_slug_from_thesis(thesis)
    if legacy_slug is not None:
        return legacy_slug
    lower = _normalize_model_match_text(thesis)
    alias_matches: List[tuple[int, str]] = []
    for slug, entry in load_model_registry().get("models", {}).items():
        candidates = [slug.replace("_", " "), str(entry.get("display_name") or "")]
        candidates.extend(str(alias) for alias in entry.get("aliases") or [])
        for candidate in candidates:
            normalized = _normalize_model_match_text(candidate)
            if normalized and _contains_model_alias(lower, normalized):
                alias_matches.append((len(normalized), slug))
    if alias_matches:
        return sorted(alias_matches, reverse=True)[0][1]
    for pattern, slug in _KEYWORD_MODEL:
        if re.search(pattern, lower):
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
    slugs = set(_hypothesis_slugs()) | set(all_slugs())
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
