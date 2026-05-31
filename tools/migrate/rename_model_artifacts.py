#!/usr/bin/env python3
"""Migrate research_cards paths and JSON model_id fields to canonical slugs."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from features_engine.src.model_registry import legacy_to_slug, resolve_model_id

_L2S = legacy_to_slug()
_HYP_DIR = re.compile(r"^HYP_(\d+)(.*)$")
_PDF_DIR = re.compile(r"^PDF_MODEL_(\d+)(.*)$")
_ID_FIELDS = ("model_id", "legacy_model_id")


def _slug_for_legacy_prefix(name: str) -> str | None:
    m = _HYP_DIR.match(name)
    if m:
        legacy = f"HYP_{m.group(1)}"
        return _L2S.get(legacy)
    m = _PDF_DIR.match(name)
    if m:
        legacy = f"PDF_MODEL_{m.group(1)}"
        return _L2S.get(legacy)
    return None


def _rename_path_component(part: str) -> str:
    for legacy, slug in _L2S.items():
        if part.startswith(legacy + "_") or part == legacy:
            return part.replace(legacy, slug, 1)
    slug = _slug_for_legacy_prefix(part)
    if slug:
        m = _HYP_DIR.match(part) or _PDF_DIR.match(part)
        suffix = m.group(2) if m else ""
        return slug + suffix
    return part


def _patch_json_obj(obj: object) -> object:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _ID_FIELDS and isinstance(v, str):
                try:
                    out[k] = resolve_model_id(v)
                except KeyError:
                    out[k] = v
            else:
                out[k] = _patch_json_obj(v)
        return out
    if isinstance(obj, list):
        return [_patch_json_obj(x) for x in obj]
    if isinstance(obj, str):
        for legacy, slug in _L2S.items():
            if legacy in obj:
                obj = obj.replace(legacy, slug)
        return obj
    return obj


def migrate_tree(root: Path, *, dry_run: bool) -> dict:
    audit: dict = {"renamed_dirs": [], "patched_files": [], "errors": []}
    if not root.is_dir():
        return audit

    # Rename deepest paths first
    all_dirs = sorted([p for p in root.rglob("*") if p.is_dir()], key=lambda p: len(p.parts), reverse=True)
    for d in all_dirs:
        new_name = _rename_path_component(d.name)
        if new_name != d.name:
            dest = d.parent / new_name
            audit["renamed_dirs"].append({"from": str(d), "to": str(dest)})
            if not dry_run:
                if dest.exists():
                    audit["errors"].append(f"dest exists: {dest}")
                else:
                    d.rename(dest)

    for fp in root.rglob("*"):
        if fp.suffix.lower() in {".json", ".yaml", ".yml", ".md"} and fp.is_file():
            try:
                text = fp.read_text(encoding="utf-8")
            except OSError:
                continue
            if not any(legacy in text for legacy in _L2S):
                continue
            if fp.suffix.lower() == ".json":
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    continue
                patched = _patch_json_obj(data)
                new_text = json.dumps(patched, indent=2) + "\n"
            else:
                new_text = text
                for legacy, slug in _L2S.items():
                    new_text = new_text.replace(legacy, slug)
            if new_text != text:
                audit["patched_files"].append(str(fp))
                if not dry_run:
                    fp.write_text(new_text, encoding="utf-8")

    audit_path = root / "migration" / "model_id_map.json"
    if not dry_run:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(json.dumps({"legacy_to_slug": _L2S, "audit": audit}, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=os.environ.get("HFT3_ARTIFACTS_ROOT", "artifacts"))
    parser.add_argument("--also-research-cards", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    repo = setup_repo_paths()
    roots = [repo / args.root]
    if args.also_research_cards:
        rc = repo / "research_cards"
        if rc.is_dir():
            roots.append(rc)
    for root in roots:
        print(f"Migrating {root} dry_run={args.dry_run}")
        audit = migrate_tree(root, dry_run=args.dry_run)
        print(f"  renamed_dirs={len(audit['renamed_dirs'])} patched_files={len(audit['patched_files'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
