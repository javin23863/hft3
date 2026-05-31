"""PDF structural model registry — separate from HYP hypothesis registry."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import yaml

from features_engine.src.model_registry import legacy_to_slug, load_model_registry, resolve_model_id

from .model_01_book_pressure import BookPressureModel
from .model_02_cross_asset_lead_lag import CrossAssetLeadLagModel
from .model_03_vpin_toxicity import VPINToxicityModel
from .model_04_hybrid_execution import HybridExecutionModel
from .model_05_dealer_hedging import DealerHedgingModel
from .model_06_dow_ym_index import DowYMIndexModel
from .model_07_treasury_ctd import TreasuryCTDModel
from .model_08_transfer_entropy import TransferEntropyModel
from .model_09_quantum_spread import QuantumSpreadDefenseModel
from .model_10_stochastic_thermo import StochasticThermoModel
from .model_11_hawkes_toxic import HawkesToxicFlowModel

_LEGACY_PDF_DEPS: Dict[str, List[str]] = {
    "PDF_MODEL_1": [],
    "PDF_MODEL_2": ["PDF_MODEL_1"],
    "PDF_MODEL_3": [],
    "PDF_MODEL_4": ["PDF_MODEL_1", "PDF_MODEL_3"],
    "PDF_MODEL_5": [],
    "PDF_MODEL_6": ["PDF_MODEL_1"],
    "PDF_MODEL_7": [],
    "PDF_MODEL_8": [],
    "PDF_MODEL_9": [],
    "PDF_MODEL_10": [],
    "PDF_MODEL_11": ["PDF_MODEL_4"],
}

_l2s = legacy_to_slug()


def _slug_deps(legacy_map: Dict[str, List[str]]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for consumer, producers in legacy_map.items():
        cslug = _l2s.get(consumer, consumer)
        out[cslug] = [_l2s.get(p, p) for p in producers]
    return out


MODEL_DEPENDENCY_MAP: Dict[str, List[str]] = _slug_deps(_LEGACY_PDF_DEPS)

PDF_MODEL_IDS = tuple(
    slug
    for slug, entry in load_model_registry().get("models", {}).items()
    if entry.get("kind") == "pdf_structural"
)

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def load_pdf_model_params() -> dict:
    path = _CONFIG_DIR / "pdf_model_params.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_structural_models():
    """Return all PDF structural model instances."""
    params = load_pdf_model_params()
    return [
        BookPressureModel(params=params),
        CrossAssetLeadLagModel(params=params),
        VPINToxicityModel(params=params),
        HybridExecutionModel(params=params),
        DealerHedgingModel(params=params),
        DowYMIndexModel(params=params),
        TreasuryCTDModel(params=params),
        TransferEntropyModel(params=params),
        QuantumSpreadDefenseModel(params=params),
        StochasticThermoModel(params=params),
        HawkesToxicFlowModel(params=params),
    ]


def get_structural_model_by_id(model_id: str):
    slug = resolve_model_id(model_id)
    for model in get_structural_models():
        if model.model_id == slug:
            return model
    raise KeyError(f"Unknown structural model: {model_id}")
