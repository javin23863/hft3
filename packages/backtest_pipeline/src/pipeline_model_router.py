"""Route workbench model ids to honest catalog-gate backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from features_engine.src.model_registry import load_model_registry, resolve_model_id
from workbench.src.registry.unified_registry import list_models

PDF_STRUCTURAL_EVAL = frozenset(
    {"BOOK_PRESSURE", "CROSS_ASSET_LEAD_LAG", "VPIN_TOXICITY", "DOW_YM_INDEX", "TRANSFER_ENTROPY", "STOCHASTIC_THERMO"}
)
PDF_DIAGNOSTICS = frozenset({"TREASURY_CTD", "QUANTUM_SPREAD_DEFENSE", "HAWKES_TOXIC_FLOW"})
PDF_HYBRID_REPLAY = frozenset({"HYBRID_EXECUTION"})
PDF_OPTIONS_FIXTURE = frozenset({"DEALER_HEDGING"})

SMOKE_HYP_SAMPLE = frozenset({"SECOND_WAVE_CONTINUATION", "SPREAD_BLOWOUT_RECOMPRESSION"})

_BACKEND_LABELS = {
    "hyp_mbo": "SignalBacktester MBO pipeline (research path)",
    "pdf_hybrid_replay": "ReplayRunner quote-engine (queue fills)",
    "pdf_structural_eval": "Structural signal eval (not queue-replay backtest)",
    "pdf_diagnostics": "Diagnostics-only (num_trades may be 0 by design)",
    "pdf_options_fixture": "Options parity fixture",
}


@dataclass(frozen=True)
class EngineRoute:
    model_id: str
    engine_kind: str
    backend_label: str


def all_model_ids() -> List[str]:
    return list_models()


def route(model_id: str) -> EngineRoute:
    slug = resolve_model_id(model_id)
    models = load_model_registry().get("models", {})
    entry = models.get(slug, {})
    if entry.get("kind") == "hypothesis":
        kind = "hyp_mbo"
    elif slug in PDF_HYBRID_REPLAY:
        kind = "pdf_hybrid_replay"
    elif slug in PDF_STRUCTURAL_EVAL:
        kind = "pdf_structural_eval"
    elif slug in PDF_DIAGNOSTICS:
        kind = "pdf_diagnostics"
    elif slug in PDF_OPTIONS_FIXTURE:
        kind = "pdf_options_fixture"
    else:
        raise KeyError(f"Unknown model_id for catalog router: {model_id}")
    return EngineRoute(model_id=model_id, engine_kind=kind, backend_label=_BACKEND_LABELS[kind])
