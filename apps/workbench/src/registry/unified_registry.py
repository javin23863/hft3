"""Load unified model registry from YAML + hypothesis registry."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import yaml

from features_engine.src.hypotheses.registry import get_active_hypotheses
from features_engine.src.model_registry import (
    all_slugs,
    legacy_to_slug,
    load_model_registry,
    resolve_model_id,
)
from features_engine.src.structural_models.registry import MODEL_DEPENDENCY_MAP
from workbench.src.core.protocol import ModelConfig

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "models.yaml"

_LANE_MAP = {
    "microsecond": "microsecond",
    "sub_10ms": "sub_10ms",
    "10_250ms": "10_250ms",
    "multi_second": "multi_second",
}

_DEFAULTS = {
    "required_datasets": ["mbo_npz"],
    "min_history_years": 10,
    "robustness_window": "discovery",
    "latency_lane": "sub_10ms",
    "execution_assumptions": "limit_queue",
    "parameter_bounds": {},
}


def _pdf_signal_field(model_id: str) -> str:
    slug = resolve_model_id(model_id)
    mapping = {
        "BOOK_PRESSURE": "OFI_zscore",
        "CROSS_ASSET_LEAD_LAG": "cross_impact_score",
        "VPIN_TOXICITY": "VPIN_percentile",
        "HYBRID_EXECUTION": "hybrid_reservation_price",
        "DEALER_HEDGING": "dealer_hedging_pressure",
        "DOW_YM_INDEX": "synthetic_Dow_pressure",
        "TREASURY_CTD": "futures_basis_signal",
        "TRANSFER_ENTROPY": "transfer_entropy",
        "QUANTUM_SPREAD_DEFENSE": "collapse_risk",
        "STOCHASTIC_THERMO": "free_energy",
        "HAWKES_TOXIC_FLOW": "toxic_cascade_score",
    }
    return mapping.get(slug, "signal")


def _pdf_diagnostics_only(model_id: str) -> bool:
    slug = resolve_model_id(model_id)
    return slug in {
        "HYBRID_EXECUTION",
        "DEALER_HEDGING",
        "TREASURY_CTD",
        "QUANTUM_SPREAD_DEFENSE",
        "HAWKES_TOXIC_FLOW",
    }


def _config_override(overrides: dict, slug: str) -> dict:
    entry = load_model_registry().get("models", {}).get(slug, {})
    legacy = entry.get("legacy_id", slug)
    return overrides.get(slug) or overrides.get(legacy) or {}


def build_models_config() -> Dict[str, ModelConfig]:
    """Build all model configs."""
    configs: Dict[str, ModelConfig] = {}

    yaml_overrides: Dict[str, dict] = {}
    if _CONFIG_PATH.is_file():
        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        yaml_overrides = raw.get("overrides", {})
        yaml_defaults = raw.get("defaults", {})
    else:
        yaml_defaults = {}

    default_bounds = yaml_defaults.get("parameter_bounds", _DEFAULTS.get("parameter_bounds", {}))
    models = load_model_registry().get("models", {})
    for slug, entry in models.items():
        ov = _config_override(yaml_overrides, slug)
        if entry.get("kind") == "hypothesis":
            hyp_id = int(entry["hyp_id"])
            configs[slug] = ModelConfig(
                model_id=slug,
                kind="hypothesis",
                name=entry.get("display_name", slug),
                hyp_id=hyp_id,
                required_datasets=ov.get("required_datasets", _DEFAULTS["required_datasets"]),
                min_history_years=ov.get("min_history_years", _DEFAULTS["min_history_years"]),
                robustness_window=ov.get("robustness_window", _DEFAULTS["robustness_window"]),
                latency_lane=ov.get("latency_lane", _DEFAULTS["latency_lane"]),
                execution_assumptions=ov.get("execution_assumptions", _DEFAULTS["execution_assumptions"]),
                parameter_bounds=ov.get("parameter_bounds", default_bounds),
            )
        elif entry.get("kind") == "pdf_structural":
            configs[slug] = ModelConfig(
                model_id=slug,
                kind="pdf",
                name=ov.get("name", entry.get("display_name", slug)),
                signal_field=ov.get("signal_field", _pdf_signal_field(slug)),
                diagnostics_only=ov.get("diagnostics_only", _pdf_diagnostics_only(slug)),
                required_datasets=ov.get("required_datasets", _DEFAULTS["required_datasets"]),
                min_history_years=ov.get("min_history_years", _DEFAULTS["min_history_years"]),
                robustness_window=ov.get("robustness_window", _DEFAULTS["robustness_window"]),
                latency_lane=ov.get(
                    "latency_lane",
                    "10_250ms"
                    if slug in {"DEALER_HEDGING", "TREASURY_CTD"}
                    else "microsecond"
                    if slug in {"TRANSFER_ENTROPY", "QUANTUM_SPREAD_DEFENSE"}
                    else "sub_10ms",
                ),
                execution_assumptions=ov.get("execution_assumptions", "signal_threshold"),
                parameter_bounds=ov.get("parameter_bounds", {}),
            )
        elif entry.get("kind") == "reinforcement_learning":
            configs[slug] = ModelConfig(
                model_id=slug,
                kind="reinforcement_learning",
                name=ov.get("name", entry.get("display_name", slug)),
                signal_field=ov.get("signal_field", entry.get("algorithm", "")),
                diagnostics_only=True,
                required_datasets=ov.get("required_datasets", ["rl_policy_artifact"]),
                min_history_years=ov.get("min_history_years", _DEFAULTS["min_history_years"]),
                robustness_window=ov.get("robustness_window", "post_hbt_only"),
                latency_lane=ov.get("latency_lane", "multi_second"),
                execution_assumptions=ov.get(
                    "execution_assumptions",
                    "pipeline_blocker:missing_uniform_hbt_adapter",
                ),
                parameter_bounds=ov.get("parameter_bounds", {}),
            )
    return configs


_MODEL_CACHE: Dict[str, "WorkbenchModel"] = {}


def get_model_by_id(model_id: str):
    from workbench.src.adapters.hypothesis_adapter import HypothesisAdapter
    from workbench.src.adapters.structural_adapter import StructuralModelAdapter

    slug = resolve_model_id(model_id)
    if slug in _MODEL_CACHE:
        return _MODEL_CACHE[slug]

    configs = build_models_config()
    if slug not in configs:
        raise KeyError(f"Unknown model: {model_id}")

    cfg = configs[slug]
    binding_raw = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "config" / "model_event_binding.yaml").read_text(encoding="utf-8")
    ) or {}
    legacy = legacy_to_slug().get(slug, slug)
    pdf_cfg = binding_raw.get("pdf", {}).get(slug) or binding_raw.get("pdf", {}).get(legacy, {})
    hyp_cfg = binding_raw.get("hypothesis", {}).get(slug) or binding_raw.get("hypothesis", {}).get(legacy, {})
    if cfg.kind == "reinforcement_learning":
        raise KeyError(f"{slug} is research-only until a uniform HBT order adapter exists")
    if pdf_cfg.get("campaign_mode") == "options_lane":
        from workbench.src.adapters.options_lane_adapter import OptionsLaneAdapter

        adapter = OptionsLaneAdapter(cfg)
    elif cfg.kind == "hypothesis" and cfg.hyp_id:
        hyps = {h.hyp_id: h for h in get_active_hypotheses()}
        hyp = hyps.get(cfg.hyp_id)
        if hyp is None:
            raise KeyError(f"{slug} not in active hypotheses (set HFT3_CROSS_ASSET=1 for 16-20)")
        adapter = HypothesisAdapter(hyp, cfg)
    else:
        adapter = StructuralModelAdapter(slug, cfg)
    _MODEL_CACHE[slug] = adapter
    return adapter


def list_models() -> List[str]:
    return sorted(build_models_config().keys())


def get_model_config(model_id: str) -> ModelConfig:
    slug = resolve_model_id(model_id)
    configs = build_models_config()
    if slug not in configs:
        raise KeyError(f"Unknown model: {model_id}")
    return configs[slug]


def dependency_map() -> Dict[str, List[str]]:
    return dict(MODEL_DEPENDENCY_MAP)
