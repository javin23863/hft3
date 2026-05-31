"""Route workbench model ids to honest catalog-gate backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from workbench.src.registry.unified_registry import list_models

PDF_STRUCTURAL_EVAL = frozenset(
    {"PDF_MODEL_1", "PDF_MODEL_2", "PDF_MODEL_3", "PDF_MODEL_6", "PDF_MODEL_8", "PDF_MODEL_10"}
)
PDF_DIAGNOSTICS = frozenset({"PDF_MODEL_7", "PDF_MODEL_9", "PDF_MODEL_11"})
PDF_HYBRID_REPLAY = frozenset({"PDF_MODEL_4"})
PDF_OPTIONS_FIXTURE = frozenset({"PDF_MODEL_5"})

SMOKE_HYP_SAMPLE = frozenset({"HYP_1", "HYP_5"})

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
    if model_id.startswith("HYP_"):
        kind = "hyp_mbo"
    elif model_id in PDF_HYBRID_REPLAY:
        kind = "pdf_hybrid_replay"
    elif model_id in PDF_STRUCTURAL_EVAL:
        kind = "pdf_structural_eval"
    elif model_id in PDF_DIAGNOSTICS:
        kind = "pdf_diagnostics"
    elif model_id in PDF_OPTIONS_FIXTURE:
        kind = "pdf_options_fixture"
    else:
        raise KeyError(f"Unknown model_id for catalog router: {model_id}")
    return EngineRoute(model_id=model_id, engine_kind=kind, backend_label=_BACKEND_LABELS[kind])
