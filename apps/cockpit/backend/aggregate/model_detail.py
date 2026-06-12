"""Per-hypothesis drill-down: how a model is CONSTRUCTED + its backtest RESULTS.

Construction is parsed from the hypothesis source via `ast` — read-only, never
imported/executed, so the cockpit stays decoupled from the (heavy) features
engine. For each hypothesis class we extract: thesis (docstring), the feature
slots it reads (`state.f('...')`), its event-context / regime gates, cross-asset
legs, the raw `evaluate()` body, and any `[[paper]]` citations. Results join the
Stage A cells + the hftbacktest card.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Optional

from .. import loaders, paths, schemas
from . import models as _models  # reuse vendored family/defect sets

_CITE = re.compile(r"\[\[(.+?)\]\]")
_cache: dict = {"mtime": None, "data": {}}


def _hyp_source_files() -> list[Path]:
    base = paths.REPO / "packages" / "features_engine" / "src" / "hypotheses"
    return [base / "modules.py", base / "vix_modules.py"]


def _string_literals(node: ast.AST) -> list[str]:
    out: list[str] = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        out.append(node.value)
    elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        for elt in node.elts:
            out.extend(_string_literals(elt))
    return out


def _extract_evaluate(method: ast.FunctionDef, src: str) -> dict:
    features: set[str] = set()
    event_gates: set[str] = set()
    regime_gates: set[str] = set()
    cross: set[str] = set()
    for sub in ast.walk(method):
        # state.f('feature', ...)
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "f":
            if sub.args and isinstance(sub.args[0], ast.Constant) and isinstance(sub.args[0].value, str):
                features.add(sub.args[0].value)
        # comparisons: state.event_context / state.regime_state in/== (...)
        if isinstance(sub, ast.Compare) and isinstance(sub.left, ast.Attribute):
            attr = sub.left.attr
            if attr in ("event_context", "regime_state"):
                tgt = event_gates if attr == "event_context" else regime_gates
                for comp in sub.comparators:
                    for lit in _string_literals(comp):
                        tgt.add(lit)
        # state.event_context.endswith('_TIGHT') / startswith
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in ("endswith", "startswith"):
            base = sub.func.value
            if isinstance(base, ast.Attribute) and base.attr == "event_context":
                for a in sub.args:
                    for lit in _string_literals(a):
                        event_gates.add(f"*{lit}" if sub.func.attr == "endswith" else f"{lit}*")
        # cross_asset_features.get('ES')
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "get":
            v = sub.func.value
            if isinstance(v, ast.Attribute) and v.attr == "cross_asset_features" and sub.args:
                for lit in _string_literals(sub.args[0]):
                    cross.add(lit)
    return {
        "features": sorted(features),
        "event_gates": sorted(event_gates),
        "regime_gates": sorted(regime_gates),
        "cross_assets": sorted(cross),
        "evaluate_source": ast.get_source_segment(src, method) or "",
    }


def _parse_all() -> dict[int, dict]:
    """hyp_id -> construction dict, cached by combined source mtime."""
    files = _hyp_source_files()
    try:
        mtime = max(f.stat().st_mtime for f in files if f.exists())
    except ValueError:
        return {}
    if _cache["mtime"] == mtime:
        return _cache["data"]

    out: dict[int, dict] = {}
    for f in files:
        src = paths.read_text(f)
        if not src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = {b.id for b in node.bases if isinstance(b, ast.Name)}
            if "BaseHypothesis" not in base_names:
                continue
            hyp_id: Optional[int] = None
            name = ""
            ev: dict = {}
            for item in node.body:
                if not isinstance(item, ast.FunctionDef):
                    continue
                if item.name == "__init__":
                    for sub in ast.walk(item):
                        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                                and sub.func.attr == "__init__" and len(sub.args) >= 2):
                            if isinstance(sub.args[0], ast.Constant) and isinstance(sub.args[0].value, int):
                                hyp_id = sub.args[0].value
                            lits = _string_literals(sub.args[1])
                            if lits:
                                name = lits[0]
                elif item.name == "evaluate":
                    ev = _extract_evaluate(item, src)
            if hyp_id is None:
                continue
            doc = (ast.get_docstring(node) or "").strip()
            out[int(hyp_id)] = {
                "class_name": node.name,
                "name": name,
                "thesis": doc,
                "citations": _CITE.findall(doc),
                **ev,
            }
    _cache["mtime"], _cache["data"] = mtime, out
    return out


def _how_it_works(c: dict) -> str:
    parts = []
    if c.get("features"):
        parts.append("reads " + ", ".join(c["features"]))
    if c.get("cross_assets"):
        parts.append("uses cross-asset leg(s) " + ", ".join(c["cross_assets"]))
    if c.get("event_gates"):
        parts.append("fires only in event context(s) " + ", ".join(c["event_gates"]))
    else:
        parts.append("evaluated on every event")
    if c.get("regime_gates"):
        parts.append("gated to regime(s) " + ", ".join(c["regime_gates"]))
    return "; ".join(parts) + "."


def _card_for(hyp_id: int) -> Optional[dict]:
    data = paths.read_json(paths.ALL_HYPOTHESES)
    if not isinstance(data, dict):
        return None
    cards = data.get("cards", {}) or {}
    return cards.get(f"HYP_{hyp_id}")


def build(hyp_id: int) -> dict:
    fam, prop_dead, cross, vix = _models._registry()
    construction = _parse_all().get(hyp_id)
    name = fam.get(hyp_id, (construction or {}).get("name", f"HYP_{hyp_id}"))
    family = _models._family_tag(hyp_id, cross, vix)
    dead = hyp_id in prop_dead

    # Stage A cells for this hyp
    cells = [c for c in loaders.stage_a_cells_compact() if c.get("hypothesis_id") == hyp_id]
    card = _card_for(hyp_id)

    out = {
        "id": hyp_id,
        "name": name,
        "family": family,
        "structurally_dead": dead,
        "generated_utc": paths.now_iso(),
        "construction": None,
        "results": {
            "stage_a_cells": cells,
            "n_event_types": len(cells),
            "total_trades": sum(c.get("total_trades", 0) or 0 for c in cells),
            "card": card,
        },
    }
    if construction:
        out["construction"] = {
            "class_name": construction["class_name"],
            "thesis": construction["thesis"],
            "how_it_works": _how_it_works(construction),
            "features": construction["features"],
            "event_gates": construction["event_gates"],
            "regime_gates": construction["regime_gates"],
            "cross_assets": construction["cross_assets"],
            "citations": construction["citations"],
            "evaluate_source": construction["evaluate_source"],
        }
        if dead:
            out["construction"]["defect_note"] = (
                "STRUCTURALLY_DEAD — no feature producer / no context / hardcoded 0 "
                "(see specs/CORRECTNESS.md). Never alive ≠ tested-and-rejected."
            )
    return out
