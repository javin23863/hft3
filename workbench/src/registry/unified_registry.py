"""Load unified 55-model registry from YAML + hypothesis registry."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import yaml

from features_engine.src.hypotheses.registry import HypothesisRegistry, get_active_hypotheses
from features_engine.src.structural_models.registry import PDF_MODEL_IDS, MODEL_DEPENDENCY_MAP
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
    mapping = {
        "PDF_MODEL_1": "OFI_zscore",
        "PDF_MODEL_2": "cross_impact_score",
        "PDF_MODEL_3": "VPIN_percentile",
        "PDF_MODEL_4": "hybrid_reservation_price",
        "PDF_MODEL_5": "dealer_hedging_pressure",
        "PDF_MODEL_6": "synthetic_Dow_pressure",
        "PDF_MODEL_7": "futures_basis_signal",
        "PDF_MODEL_8": "transfer_entropy",
        "PDF_MODEL_9": "collapse_risk",
        "PDF_MODEL_10": "free_energy",
        "PDF_MODEL_11": "toxic_cascade_score",
    }
    return mapping.get(model_id, "signal")


def _pdf_diagnostics_only(model_id: str) -> bool:
    return model_id in {"PDF_MODEL_4", "PDF_MODEL_5", "PDF_MODEL_7", "PDF_MODEL_9", "PDF_MODEL_11"}


def build_models_config() -> Dict[str, ModelConfig]:
    """Build all 55 model configs (44 HYP + 11 PDF)."""
    hyp_reg = HypothesisRegistry()
    configs: Dict[str, ModelConfig] = {}

    yaml_overrides: Dict[str, dict] = {}
    if _CONFIG_PATH.is_file():
        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        yaml_overrides = raw.get("overrides", {})

    active = {h.hyp_id: h for h in get_active_hypotheses()}
    for hyp_id in range(1, 45):
        mid = f"HYP_{hyp_id}"
        ov = yaml_overrides.get(mid, {})
        configs[mid] = ModelConfig(
            model_id=mid,
            kind="hypothesis",
            name=hyp_reg.get_hypothesis_name(hyp_id),
            hyp_id=hyp_id,
            required_datasets=ov.get("required_datasets", _DEFAULTS["required_datasets"]),
            min_history_years=ov.get("min_history_years", _DEFAULTS["min_history_years"]),
            robustness_window=ov.get("robustness_window", _DEFAULTS["robustness_window"]),
            latency_lane=ov.get("latency_lane", _DEFAULTS["latency_lane"]),
            execution_assumptions=ov.get("execution_assumptions", _DEFAULTS["execution_assumptions"]),
            parameter_bounds=ov.get("parameter_bounds", {}),
        )

    for pid in PDF_MODEL_IDS:
        ov = yaml_overrides.get(pid, {})
        configs[pid] = ModelConfig(
            model_id=pid,
            kind="pdf",
            name=ov.get("name", pid),
            signal_field=ov.get("signal_field", _pdf_signal_field(pid)),
            diagnostics_only=ov.get("diagnostics_only", _pdf_diagnostics_only(pid)),
            required_datasets=ov.get("required_datasets", _DEFAULTS["required_datasets"]),
            min_history_years=ov.get("min_history_years", _DEFAULTS["min_history_years"]),
            robustness_window=ov.get("robustness_window", _DEFAULTS["robustness_window"]),
            latency_lane=ov.get("latency_lane", "10_250ms" if pid in {"PDF_MODEL_5", "PDF_MODEL_7"} else "microsecond" if pid in {"PDF_MODEL_8", "PDF_MODEL_9"} else "sub_10ms"),
            execution_assumptions=ov.get("execution_assumptions", "signal_threshold"),
            parameter_bounds=ov.get("parameter_bounds", {}),
        )
    return configs


_MODEL_CACHE: Dict[str, "WorkbenchModel"] = {}


def get_model_by_id(model_id: str):
    from workbench.src.adapters.hypothesis_adapter import HypothesisAdapter
    from workbench.src.adapters.structural_adapter import StructuralModelAdapter

    if model_id in _MODEL_CACHE:
        return _MODEL_CACHE[model_id]

    configs = build_models_config()
    if model_id not in configs:
        raise KeyError(f"Unknown model: {model_id}")

    cfg = configs[model_id]
    binding_raw = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "config" / "model_event_binding.yaml").read_text(encoding="utf-8")
    ) or {}
    pdf_cfg = binding_raw.get("pdf", {}).get(model_id, {})
    if pdf_cfg.get("campaign_mode") == "options_lane":
        from workbench.src.adapters.options_lane_adapter import OptionsLaneAdapter

        adapter = OptionsLaneAdapter(cfg)
    elif cfg.kind == "hypothesis" and cfg.hyp_id:
        hyps = {h.hyp_id: h for h in get_active_hypotheses()}
        hyp = hyps.get(cfg.hyp_id)
        if hyp is None:
            raise KeyError(f"{model_id} not in active hypotheses (set HFT3_CROSS_ASSET=1 for 16-20)")
        adapter = HypothesisAdapter(hyp, cfg)
    else:
        adapter = StructuralModelAdapter(model_id, cfg)
    _MODEL_CACHE[model_id] = adapter
    return adapter


def list_models() -> List[str]:
    return sorted(build_models_config().keys())


def dependency_map() -> Dict[str, List[str]]:
    return dict(MODEL_DEPENDENCY_MAP)
