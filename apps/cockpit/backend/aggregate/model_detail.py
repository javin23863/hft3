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
import os
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

from .. import loaders, paths, schemas
from . import lifecycle as lifecycle_agg
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


def _rel(path: Path) -> str:
    try:
        return path.relative_to(paths.REPO).as_posix()
    except ValueError:
        return path.as_posix()


def _artifact_url(path: Path) -> str:
    return "/api/artifact?" + urlencode({"path": _rel(path)})


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _slug_from_name(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()
    return re.sub(r"_+", "_", slug)


def _slug_for_hyp_id(hyp_id: int, name: str) -> str:
    try:
        from features_engine.src.model_registry import get_slug_for_hyp_id

        return get_slug_for_hyp_id(hyp_id)
    except Exception:
        return _slug_from_name(name)


def _file_surface(kind: str, label: str, path: Path, **extra: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": label,
        "path": _rel(path),
        "url": _artifact_url(path),
        "modified_utc": paths.mtime_iso(path),
        **extra,
    }


def _latest_file(files: list[Path]) -> Path | None:
    existing = [path for path in files if path.is_file()]
    if not existing:
        return None
    return max(existing, key=_mtime)


def _workbench_dir_matches(name: str, slug: str, legacy_id: str) -> bool:
    return (
        name == slug
        or name.startswith(f"{slug}_")
        or name == legacy_id
        or name.startswith(f"{legacy_id}_")
    )


def _matching_workbench_dirs(slug: str, hyp_id: int) -> list[Path]:
    slug_upper = slug.upper()
    legacy_id = f"HYP_{hyp_id}"
    roots = [
        paths.REPO / "artifacts" / "workbench_runs",
        paths.REPO / "artifacts" / "research_cards" / "workbench_runs",
        paths.REPO / "research_cards" / "workbench_runs",
    ]
    matches: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if not child.is_dir():
                continue
            upper = child.name.upper()
            if _workbench_dir_matches(upper, slug_upper, legacy_id):
                matches.append(child)
    return matches


def _latest_workbench_artifact(slug: str, hyp_id: int, filename: str) -> Path | None:
    files: list[Path] = []
    for run_dir in _matching_workbench_dirs(slug, hyp_id):
        files.extend(path for path in run_dir.rglob(filename) if path.is_file())
    return _latest_file(files)


def _artifact_roots() -> tuple[Path, ...]:
    roots = (
        paths.REPO / "artifacts",
        paths.REPO / "research_cards",
        paths.REPO / "reports",
        paths.REPO / "runtime" / "reports",
        paths.REPO / "runtime" / "latency_reports",
        paths.REPO / "runtime" / "validation",
        paths.REPO / "runtime" / "monitor",
        paths.REPO / "runtime" / "workbench",
    )
    return tuple(root.resolve() for root in roots)


def _safe_status_manifest_path(requested: str) -> Path | None:
    raw = requested.strip().replace("\\", "/")
    rel = Path(raw)
    if not raw or rel.is_absolute() or ".." in rel.parts:
        return None
    try:
        candidate = (paths.REPO / rel).resolve()
    except (OSError, ValueError):
        return None
    if not any(candidate == root or root in candidate.parents for root in _artifact_roots()):
        return None
    return candidate if candidate.is_file() else None


def _latest_paid_manifest(status: dict[str, Any] | None = None) -> Path | None:
    if isinstance(status, dict):
        rel = status.get("manifest_artifact")
        if isinstance(rel, str) and rel:
            candidate = _safe_status_manifest_path(rel)
            if candidate is not None:
                return candidate
    root = paths.pipeline_runs_root()
    if not root.is_dir():
        return None
    return _latest_file([path for path in root.glob("*/paid_screen_run_manifest.json")])


def _first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data.get(key) not in (None, ""):
            return data.get(key)
    return None


_MODEL_ID_KEYS = {
    "model_id",
    "legacy_id",
    "hypothesis_id",
    "hyp_id",
    "primary_model_id",
    "resolved_model_id",
    "slug",
}
_MODEL_METADATA_KEYS = {
    "candidate",
    "candidate_metadata",
    "metadata",
    "model",
    "promoted",
    "rejected",
    "scenario",
    "scenarios",
    "screening_artifact",
    "selected_candidate",
    "unit_results",
}


def _matches_model(data: Any, slug: str, hyp_id: int, *, depth: int = 0) -> bool:
    if depth > 5:
        return False
    targets = {slug.upper(), f"HYP_{hyp_id}", str(hyp_id)}
    if isinstance(data, dict):
        for key, value in data.items():
            key_l = str(key).lower()
            if key_l in _MODEL_ID_KEYS and isinstance(value, (str, int)):
                if str(value).upper() in targets:
                    return True
            if key_l in _MODEL_METADATA_KEYS and _matches_model(value, slug, hyp_id, depth=depth + 1):
                return True
    elif isinstance(data, list):
        return any(_matches_model(item, slug, hyp_id, depth=depth + 1) for item in data[:200])
    return False


def _latest_hftbacktest_summary(slug: str, hyp_id: int) -> Path | None:
    root = paths.hftbacktest_realism_root()
    if not root.is_dir():
        return None
    matched: list[Path] = []
    for summary in root.glob("*/replay_summary.json"):
        if not summary.is_file():
            continue
        data = paths.read_json(summary)
        manifest = paths.read_json(summary.parent / "input_manifest.json")
        if _matches_model(data, slug, hyp_id) or _matches_model(manifest, slug, hyp_id):
            matched.append(summary)
    return _latest_file(matched)


def _result_surfaces(hyp_id: int, name: str) -> list[dict[str, Any]]:
    slug = _slug_for_hyp_id(hyp_id, name)
    workbench_base = os.environ.get("COCKPIT_WORKBENCH_URL", "http://localhost:8501").rstrip("/")
    surfaces: list[dict[str, Any]] = [
        {
            "kind": "url",
            "label": "Workbench viewer",
            "url": f"{workbench_base}/?{urlencode({'source': 'workbench_campaign', 'model': slug})}",
            "model_slug": slug,
            "legacy_id": f"HYP_{hyp_id}",
        }
    ]

    report = _latest_workbench_artifact(slug, hyp_id, "report.md")
    if report is not None:
        surfaces.append(
            _file_surface("workbench_report", "Latest Workbench report", report, model_slug=slug)
        )
    research_card = _latest_workbench_artifact(slug, hyp_id, "research_card.json")
    if research_card is not None:
        surfaces.append(
            _file_surface(
                "workbench_research_card",
                "Latest Workbench research card",
                research_card,
                model_slug=slug,
            )
        )

    if paths.STAGE_A_RESULT.is_file():
        surfaces.append(
            _file_surface(
                "stage_a_result",
                "Global Stage A result",
                paths.STAGE_A_RESULT,
                scope="global",
            )
        )
    if paths.STAGE_A_SURVIVORS.is_file():
        surfaces.append(
            _file_surface(
                "stage_a_survivors",
                "Global Stage A survivors",
                paths.STAGE_A_SURVIVORS,
                scope="global",
            )
        )

    vbt_status = paths.read_json(paths.VBT_FULL_STATUS)
    if paths.VBT_FULL_STATUS.is_file():
        status_extra = {}
        if isinstance(vbt_status, dict):
            status_extra = {
                "state": vbt_status.get("state") or vbt_status.get("status"),
                "run_id": vbt_status.get("run_id"),
                "expected_work_units": _first_present(vbt_status, "expected_work_units", "expected"),
                "completed_work_units": _first_present(vbt_status, "completed_work_units", "completed"),
            }
            for surface_key, status_keys in (
                ("failed_work_units", ("failed_work_units", "failed")),
                ("skipped_work_units", ("skipped_work_units", "skipped")),
            ):
                if any(key in vbt_status for key in status_keys):
                    status_extra[surface_key] = _first_present(vbt_status, *status_keys)
        surfaces.append(
            _file_surface(
                "vectorbt_paid_status",
                "Global VectorBT paid status",
                paths.VBT_FULL_STATUS,
                scope="global",
                **status_extra,
            )
        )
    vbt_manifest = _latest_paid_manifest(vbt_status if isinstance(vbt_status, dict) else None)
    if vbt_manifest is not None:
        surfaces.append(
            _file_surface(
                "vectorbt_paid_manifest",
                "Global VectorBT paid manifest",
                vbt_manifest,
                scope="global",
            )
        )

    hbt_summary = _latest_hftbacktest_summary(slug, hyp_id)
    if hbt_summary is not None:
        surfaces.append(_file_surface("hftbacktest_replay_summary", "Latest HftBacktest replay summary", hbt_summary))
    return surfaces


def _lifecycle_block(hyp_id: int) -> dict:
    block = lifecycle_agg.row_for_hypothesis(hyp_id)
    if not block.get("tracked"):
        return block
    links = dict(block.get("evidence_links") or {})
    block["evidence_links"] = links
    return block


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
            "surfaces": _result_surfaces(hyp_id, name),
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
    out["lifecycle"] = _lifecycle_block(hyp_id)
    return out
