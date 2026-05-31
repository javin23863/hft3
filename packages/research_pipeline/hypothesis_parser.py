"""Natural-language hypothesis → structured ParsedHypothesis."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from features_engine.src.model_registry import all_slugs, load_model_registry

from research_pipeline.llm import generate_json
from research_pipeline.types import ParsedHypothesis

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


def _hypothesis_slugs() -> List[str]:
    reg = load_model_registry().get("models", {})
    return sorted(k for k, v in reg.items() if v.get("kind") == "hypothesis")


def _match_model(thesis: str) -> str:
    lower = thesis.lower()
    for pattern, slug in _KEYWORD_MODEL:
        if re.search(pattern, lower):
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


def _from_llm_dict(thesis: str, data: Dict[str, Any]) -> ParsedHypothesis:
    slugs = set(_hypothesis_slugs()) | set(all_slugs())
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
        source="ollama",
    )


def _from_hypothesis_packet(thesis: str, data: Dict[str, Any]) -> ParsedHypothesis:
    slugs = set(_hypothesis_slugs()) | set(all_slugs())
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
        source="ollama",
    )


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
    user = f"Thesis:\n{thesis}\n\nAllowed primary_model_id values:\n" + ", ".join(slugs[:44])
    data, err = generate_json(_PARSE_SYSTEM, user)
    if data is None:
        parsed = _heuristic_parse(thesis)
        parsed.source = "heuristic"
        return parsed
    return _from_llm_dict(thesis, data)
