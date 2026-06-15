#!/usr/bin/env python3
"""Generate apps/workbench/config/model_event_binding.yaml from modules.py AST + PDF overrides."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import features_engine_root, setup_repo_paths, workbench_root

setup_repo_paths()

from economic_event_universe.registry import default_cme_symbols
from features_engine.src.model_registry import get_slug_for_hyp_id, legacy_to_slug, load_model_registry

MODULES = features_engine_root() / "src" / "hypotheses" / "modules.py"
OUT = workbench_root() / "config" / "model_event_binding.yaml"

CATALOG_ALL_CONTEXTS = "catalog_all_contexts"

_L2S = legacy_to_slug()
DEFAULT_RESEARCH_SYMBOL = "MES.v.0"
SYMBOL_SOURCE = "economic_event_universe.defaults.symbol_universe_default"
PRIMARY_RESEARCH_SYMBOLS = {
    "NQ_MNQ_LEAD_LAG": "MNQ.v.0",
    "ES_NQ_DIVERGENCE_SNAPBACK": "ES.v.0",
    "ES_MES_LEAD_LAG": "MES.v.0",
    "ZN_ZB_ES_NQ_MACRO_IMPULSE": "ZN.v.0",
}
PDF_OVERRIDES = {
    _L2S["PDF_MODEL_5"]: {
        "required_datasets": ["options_chain"],
        "latency_lane": "10_250ms",
        "campaign_mode": "options_lane",
    },
}


def _with_workbench_research_symbol(model_id: str, cfg: dict) -> dict:
    if cfg.get("campaign_mode") == "options_lane":
        return cfg

    primary = PRIMARY_RESEARCH_SYMBOLS.get(model_id, DEFAULT_RESEARCH_SYMBOL)
    universe = list(default_cme_symbols())
    if primary not in universe:
        return cfg
    cfg["research_symbol"] = primary
    cfg["symbol_universe"] = universe
    cfg["symbol_source"] = SYMBOL_SOURCE
    return cfg


def _extract_hyp_bindings() -> dict[str, dict]:
    src = MODULES.read_text(encoding="utf-8")
    tree = ast.parse(src)
    bindings: dict[str, dict] = {}
    current_hyp: int | None = None

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.FunctionDef) and stmt.name == "__init__":
                    for call in ast.walk(stmt):
                        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
                            if call.func.attr == "__init__" and len(call.args) >= 1:
                                if isinstance(call.args[0], ast.Constant) and isinstance(
                                    call.args[0].value, int
                                ):
                                    current_hyp = call.args[0].value
                    break
            if current_hyp is None:
                continue
            contexts: set[str] = set()
            for fn in node.body:
                if isinstance(fn, ast.FunctionDef) and fn.name == "evaluate":
                    for lit in ast.walk(fn):
                        if isinstance(lit, ast.Constant) and isinstance(lit.value, str):
                            if lit.value.endswith("_TIGHT") or lit.value.endswith("_CLOSE") or "FLATTEN" in lit.value or lit.value in {
                                "CASH_EQUITY_OPEN",
                                "PROP_REOPEN",
                                "NEWS_RESTRICTION",
                                "APEX_FLATTEN",
                                "TPT_FLATTEN",
                            }:
                                contexts.add(lit.value)
            slug = get_slug_for_hyp_id(current_hyp)
            if contexts:
                bindings[slug] = {"required_event_contexts": sorted(contexts)}
            else:
                bindings[slug] = {"event_context_policy": CATALOG_ALL_CONTEXTS}
            current_hyp = None

    hyp_ids = sorted(
        int(entry["hyp_id"])
        for entry in load_model_registry().get("models", {}).values()
        if entry.get("kind") == "hypothesis" and entry.get("hyp_id") is not None
    )
    for hid in hyp_ids:
        slug = get_slug_for_hyp_id(hid)
        bindings.setdefault(slug, {"event_context_policy": CATALOG_ALL_CONTEXTS})

    return dict(sorted(bindings.items()))


def main() -> int:
    doc = build_doc()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    print(f"Wrote {OUT}")
    return 0


def build_doc() -> dict:
    hyp = _extract_hyp_bindings()
    hyp = {
        model_id: _with_workbench_research_symbol(model_id, cfg)
        for model_id, cfg in hyp.items()
    }

    pdf = {}
    for i in range(1, 12):
        legacy = f"PDF_MODEL_{i}"
        slug = _L2S[legacy]
        cfg = dict(PDF_OVERRIDES.get(slug, {"event_context_policy": CATALOG_ALL_CONTEXTS}))
        pdf[slug] = cfg

    return {
        "authority": [
            "chicago_cme_microstructure_a_plus_developer_handoff.pdf",
            "hft_framework_developer_prompt.pdf",
            "features_engine/src/hypotheses/modules.py",
            "features_engine/src/regime/event_context.py",
            "packages/economic_event_universe/config/event_universe.yaml",
        ],
        "hypothesis": hyp,
        "pdf": pdf,
    }


if __name__ == "__main__":
    raise SystemExit(main())
