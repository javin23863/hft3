"""Model catalog loader — display metadata, roles, phase budgets."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import yaml

from features_engine.src.hypotheses.registry import HypothesisRegistry
from workbench.src.core.composition import CatalogEntry, DefensiveStub, ModelComposition, ModelRole, Phase
from workbench.src.registry.unified_registry import build_models_config

_CATALOG_PATH = Path(__file__).resolve().parents[2] / "config" / "model_catalog.yaml"

_DEFENSIVE_IDS = frozenset(
    {
        "PDF_MODEL_3",
        "PDF_MODEL_4",
        "PDF_MODEL_9",
        "PDF_MODEL_11",
        "HYP_14",
        "HYP_29",
        "HYP_32",
        "HYP_39",
        "HYP_40",
    }
)
_HYBRID_IDS = frozenset({"PDF_MODEL_5", "PDF_MODEL_7"})

_LANE_BUDGET_US = {
    "microsecond": 50.0,
    "sub_10ms": 2500.0,
    "10_250ms": 10000.0,
    "multi_second": 100000.0,
}


def _default_role(model_id: str, diagnostics_only: bool) -> ModelRole:
    if model_id in _DEFENSIVE_IDS:
        return "defensive"
    if model_id in _HYBRID_IDS or diagnostics_only:
        return "hybrid"
    return "alpha"


def _hyp_description(hyp_id: int, name: str) -> str:
    templates = {
        5: "Detects spread expansion and mean-reversion after liquidity shock. Fades overextended moves when book refills.",
        1: "Second-wave continuation after initial impulse; rides sustained aggressor flow when refill confirms direction.",
        2: "Stop-run exhaustion fade; fades extended moves when stop cascades exhaust and book stabilizes.",
    }
    if hyp_id in templates:
        return templates[hyp_id]
    return f"{name}. Event-time microstructure hypothesis evaluated on MBO replay windows."


def load_catalog_raw(repo_root: Optional[Path] = None) -> dict:
    path = (repo_root or Path(__file__).resolve().parents[2]) / "config" / "model_catalog.yaml"
    if not path.is_file():
        path = _CATALOG_PATH
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_catalog(repo_root: Optional[Path] = None) -> Dict[str, CatalogEntry]:
    raw = load_catalog_raw(repo_root)
    overrides = {k: v for k, v in raw.items() if k not in ("defaults",) and isinstance(v, dict)}
    configs = build_models_config()
    hyp_reg = HypothesisRegistry()
    out: Dict[str, CatalogEntry] = {}

    for model_id, cfg in configs.items():
        ov = overrides.get(model_id, {})
        if model_id.startswith("HYP_"):
            hyp_id = int(model_id.split("_")[1])
            display = ov.get("display_name") or cfg.name or hyp_reg.get_hypothesis_name(hyp_id)
            desc = ov.get("description") or _hyp_description(hyp_id, display)
        else:
            display = ov.get("display_name") or cfg.name or model_id
            desc = ov.get("description") or f"{display}. PDF structural model from registry."

        role: ModelRole = ov.get("role") or _default_role(model_id, cfg.diagnostics_only)
        phase: Phase = ov.get("default_phase") or ("continuous" if role == "defensive" and model_id == "PDF_MODEL_3" else "during")
        if phase == "whole_event":
            phase = "during"
        budget = float(ov.get("budget_us", _LANE_BUDGET_US.get(cfg.latency_lane, 2500.0)))
        requires = tuple(str(x) for x in ov.get("requires", ()))

        out[model_id] = CatalogEntry(
            model_id=model_id,
            display_name=str(display),
            description=str(desc)[:240],
            role=role,
            default_phase=phase,
            budget_us=budget,
            blocks_trade=bool(ov.get("blocks_trade", False)),
            requires=requires,
        )
    return out


def get_catalog_entry(model_id: str, repo_root: Optional[Path] = None) -> CatalogEntry:
    cat = load_catalog(repo_root)
    if model_id not in cat:
        raise KeyError(f"Unknown catalog model: {model_id}")
    return cat[model_id]


def list_by_role(role: ModelRole, repo_root: Optional[Path] = None) -> List[CatalogEntry]:
    return sorted(
        [e for e in load_catalog(repo_root).values() if e.role == role],
        key=lambda e: e.model_id,
    )


def resolve_stub_dependencies(stubs: List[DefensiveStub], repo_root: Optional[Path] = None) -> List[DefensiveStub]:
    """Auto-add required defensive dependencies (e.g. PDF_MODEL_4 for PDF_MODEL_11)."""
    cat = load_catalog(repo_root)
    present = {s.model_id for s in stubs if s.enabled}
    out = list(stubs)
    changed = True
    while changed:
        changed = False
        for stub in list(out):
            if not stub.enabled:
                continue
            entry = cat.get(stub.model_id)
            if not entry:
                continue
            for req in entry.requires:
                if req not in present:
                    req_entry = cat[req]
                    out.append(
                        DefensiveStub(
                            model_id=req,
                            phase=req_entry.default_phase,
                            budget_us=req_entry.budget_us,
                            enabled=True,
                        )
                    )
                    present.add(req)
                    changed = True
    return out


def validate_composition(composition: ModelComposition, repo_root: Optional[Path] = None) -> List[str]:
    errors: List[str] = []
    cat = load_catalog(repo_root)
    if composition.primary_model_id not in cat:
        errors.append(f"Unknown primary model: {composition.primary_model_id}")
    primary = cat.get(composition.primary_model_id)
    if primary and primary.role == "defensive":
        errors.append("Primary model should be alpha or hybrid signal, not defensive-only")

    stubs = resolve_stub_dependencies(composition.defensive_stubs, repo_root)
    for stub in stubs:
        if not stub.enabled:
            continue
        if stub.model_id not in cat:
            errors.append(f"Unknown defensive stub: {stub.model_id}")
            continue
        if cat[stub.model_id].role == "alpha" and stub.model_id == composition.primary_model_id:
            errors.append(f"Primary cannot also be defensive stub: {stub.model_id}")

    return errors


def phase_budget_summary(composition: ModelComposition, repo_root: Optional[Path] = None) -> dict[str, float]:
    """Sum budget_us by phase for enabled stubs."""
    stubs = resolve_stub_dependencies(composition.defensive_stubs, repo_root)
    totals: dict[str, float] = {"before": 0.0, "during": 0.0, "after": 0.0, "continuous": 0.0}
    for stub in stubs:
        if stub.enabled:
            totals[stub.phase] = totals.get(stub.phase, 0.0) + stub.budget_us
    return totals
