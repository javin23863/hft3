"""Quarantine -> gauntlet bridge (the genuinely-new connective piece).

Materializes a quarantined candidate into something the EXISTING gauntlet
consumes, WITHOUT relaxing the safety property that the production hypothesis
registry is never auto-mutated:

  * param-tweak  -> a stage_a_survivors-shaped stub fed to run_event_universe
                    via --from-stage-a/--cells (the hypothesis already exists).
  * hyp-tweak    -> a SCRATCH-namespace id (>=900) + a manifest; the production
                    registry is untouched. (Authoring the variant's signal code
                    is the human/worker boundary; only rearm.py may ever register
                    into production, and only post full gauntlet + shadow + gates.)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

SCRATCH_ID_BASE = 900


def materialize_param_candidate(*, hyp_id: int, event_type: str, band_ms: float, params: dict, slug: str) -> dict:
    """A survivors-shaped stub restricting the gauntlet to this one re-tuned cell."""
    return {
        "band_ms": band_ms,
        "tested_cells": [{
            "hyp_id": hyp_id, "event_type": event_type, "band_ms": band_ms,
            "p": 0.0, "adj_alpha": 0.0, "significant_bh": True,
            "params": params, "slug": slug,
        }],
        "pass_through": [hyp_id],
        "feature_version": "fs_v1",
        "generated_from": "quarantine_bridge.param",
    }


def assign_scratch_id(existing_ids) -> int:
    """Next reserved scratch id (>=900) that does not collide with production."""
    ceiling = max([SCRATCH_ID_BASE - 1, *[int(i) for i in existing_ids if str(i).isdigit()]])
    return max(SCRATCH_ID_BASE, ceiling + 1)


def write_bridge_manifest(path: Path, manifest: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)
    return path


def scratch_registry_enabled() -> bool:
    """The scratch namespace only unions into get_active_hypotheses when this is set.

    Default OFF => production registry is exactly the 50 hypotheses, never a
    half-baked auto-variant.
    """
    return bool(os.environ.get("HFT3_SCRATCH_HYP_REGISTRY"))
