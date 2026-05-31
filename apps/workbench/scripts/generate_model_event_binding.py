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

from features_engine.src.model_registry import get_slug_for_hyp_id, legacy_to_slug

MODULES = features_engine_root() / "src" / "hypotheses" / "modules.py"
OUT = workbench_root() / "config" / "model_event_binding.yaml"
WF = workbench_root() / "config" / "walk_forward.yaml"

DEFAULT_MACRO = ["CPI_TIGHT", "NFP_TIGHT"]

_L2S = legacy_to_slug()
PDF_OVERRIDES = {
    _L2S["PDF_MODEL_5"]: {
        "required_datasets": ["options_chain"],
        "latency_lane": "10_250ms",
        "campaign_mode": "options_lane",
    },
}


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
                bindings[slug] = {"default_macro_contexts": list(DEFAULT_MACRO)}
            current_hyp = None

    for hid in range(1, 45):
        slug = get_slug_for_hyp_id(hid)
        bindings.setdefault(slug, {"default_macro_contexts": list(DEFAULT_MACRO)})

    return dict(sorted(bindings.items()))


def main() -> int:
    wf = yaml.safe_load(WF.read_text(encoding="utf-8")) or {}
    default_macro = wf.get("default_macro_contexts", DEFAULT_MACRO)

    hyp = _extract_hyp_bindings()
    for slug, cfg in hyp.items():
        if "default_macro_contexts" in cfg:
            cfg["default_macro_contexts"] = list(default_macro)

    pdf = {}
    for i in range(1, 12):
        legacy = f"PDF_MODEL_{i}"
        slug = _L2S[legacy]
        pdf[slug] = dict(PDF_OVERRIDES.get(slug, {"default_macro_contexts": list(default_macro)}))

    doc = {
        "authority": [
            "chicago_cme_microstructure_a_plus_developer_handoff.pdf",
            "hft_framework_developer_prompt.pdf",
            "features_engine/src/hypotheses/modules.py",
            "features_engine/src/regime/event_context.py",
        ],
        "hypothesis": hyp,
        "pdf": pdf,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
