"""Route workbench model ids to honest catalog-gate backends."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import List

from features_engine.src.model_registry import load_model_registry, resolve_model_id
from workbench.src.registry.unified_registry import list_models

# Single source of truth for the PDF structural role sets lives in
# model_execution_contracts (no-cherry-pick v2). Re-exported here so existing
# importers keep working, but membership is defined once.
from backtest_pipeline.src.model_execution_contracts import (
    PDF_DIAGNOSTICS,
    PDF_HYBRID_REPLAY,
    PDF_OPTIONS_FIXTURE,
    PDF_STRUCTURAL_EVAL,
)

SMOKE_HYP_SAMPLE = frozenset({"SECOND_WAVE_CONTINUATION", "SPREAD_BLOWOUT_RECOMPRESSION"})

_BACKEND_LABELS = {
    "hyp_mbo": "ReplaySession MBO pipeline (research path)",
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
        print(f"Warning: unknown model_id for catalog router: {model_id}, routing as hyp_mbo", file=sys.stderr)
        kind = "hyp_mbo"
    return EngineRoute(model_id=model_id, engine_kind=kind, backend_label=_BACKEND_LABELS[kind])
