"""Parse composition from CLI flags."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from workbench.src.core.composition import DefensiveStub, ModelComposition
from workbench.src.registry.model_catalog import get_catalog_entry, load_catalog


def parse_defensive_flag(spec: str) -> List[DefensiveStub]:
    """Parse --defensive PDF_MODEL_9:before,PDF_MODEL_11:during[:budget_us]."""
    stubs: List[DefensiveStub] = []
    if not spec.strip():
        return stubs
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        pieces = part.split(":")
        model_id = pieces[0]
        phase = pieces[1] if len(pieces) > 1 else get_catalog_entry(model_id).default_phase
        budget = float(pieces[2]) if len(pieces) > 2 else get_catalog_entry(model_id).budget_us
        stubs.append(DefensiveStub(model_id=model_id, phase=phase, budget_us=budget))
    return stubs


def load_composition(
    primary_model_id: str,
    *,
    composition_path: Optional[Path] = None,
    defensive_spec: Optional[str] = None,
) -> ModelComposition:
    if composition_path and composition_path.is_file():
        data = json.loads(composition_path.read_text(encoding="utf-8"))
        comp = ModelComposition.from_dict(data)
        if comp.primary_model_id != primary_model_id:
            raise ValueError(
                f"composition primary {comp.primary_model_id!r} != CLI --model {primary_model_id!r}"
            )
        return comp
    stubs = parse_defensive_flag(defensive_spec or "")
    return ModelComposition(primary_model_id=primary_model_id, defensive_stubs=stubs)
