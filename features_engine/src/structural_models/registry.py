"""PDF structural model registry — separate from HYP hypothesis registry."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import yaml

from .model_01_book_pressure import BookPressureModel
from .model_02_cross_asset_lead_lag import CrossAssetLeadLagModel
from .model_03_vpin_toxicity import VPINToxicityModel
from .model_04_hybrid_execution import HybridExecutionModel
from .model_05_dealer_hedging import DealerHedgingModel
from .model_06_dow_ym_index import DowYMIndexModel
from .model_07_treasury_ctd import TreasuryCTDModel

PDF_MODEL_IDS = (
    "PDF_MODEL_1",
    "PDF_MODEL_2",
    "PDF_MODEL_3",
    "PDF_MODEL_4",
    "PDF_MODEL_5",
    "PDF_MODEL_6",
    "PDF_MODEL_7",
)

# Directed dependency map: consumer -> list of producers
MODEL_DEPENDENCY_MAP: Dict[str, List[str]] = {
    "PDF_MODEL_1": [],
    "PDF_MODEL_2": ["PDF_MODEL_1"],
    "PDF_MODEL_3": [],
    "PDF_MODEL_4": ["PDF_MODEL_1", "PDF_MODEL_3"],
    "PDF_MODEL_5": [],
    "PDF_MODEL_6": ["PDF_MODEL_1"],
    "PDF_MODEL_7": [],
}

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def load_pdf_model_params() -> dict:
    path = _CONFIG_DIR / "pdf_model_params.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_structural_models():
    """Return all seven PDF structural model instances."""
    params = load_pdf_model_params()
    return [
        BookPressureModel(params=params),
        CrossAssetLeadLagModel(params=params),
        VPINToxicityModel(params=params),
        HybridExecutionModel(params=params),
        DealerHedgingModel(params=params),
        DowYMIndexModel(params=params),
        TreasuryCTDModel(params=params),
    ]


def get_structural_model_by_id(model_id: str):
    for model in get_structural_models():
        if model.model_id == model_id:
            return model
    raise KeyError(f"Unknown structural model: {model_id}")
