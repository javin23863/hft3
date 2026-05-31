#!/usr/bin/env python3
"""Rewrite workbench YAML config keys from legacy HYP_N/PDF_MODEL_N to slugs."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

import yaml

from features_engine.src.model_registry import legacy_to_slug


def _rewrite_keys(section: dict) -> dict:
    if not isinstance(section, dict):
        return section
    out = {}
    for key, val in section.items():
        new_key = legacy_to_slug().get(key, key)
        if isinstance(val, dict):
            val = dict(val)
            if "requires" in val and isinstance(val["requires"], list):
                val["requires"] = [
                    legacy_to_slug().get(x, x) for x in val["requires"]
                ]
        out[new_key] = val
    return out


def migrate_file(path: Path) -> None:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    changed = False
    for top in ("hypothesis", "pdf", "overrides"):
        if top in raw and isinstance(raw[top], dict):
            raw[top] = _rewrite_keys(raw[top])
            changed = True
    if top := raw.keys():
        pass
    # model_catalog.yaml: top-level model keys (not defaults)
    if path.name == "model_catalog.yaml":
        new_raw = {}
        for key, val in raw.items():
            if key == "defaults" or not isinstance(val, dict):
                new_raw[key] = val
                continue
            new_key = legacy_to_slug().get(key, key)
            if isinstance(val, dict) and "requires" in val:
                val = dict(val)
                val["requires"] = [
                    legacy_to_slug().get(x, x) for x in val.get("requires", [])
                ]
            new_raw[new_key] = val
            changed = True
        raw = new_raw
    if changed or path.name == "model_catalog.yaml":
        path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
        print(f"Updated {path}")


def main() -> int:
    from hft3_bootstrap import workbench_root

    cfg = workbench_root() / "config"
    for name in ("model_event_binding.yaml", "models.yaml", "model_catalog.yaml"):
        p = cfg / name
        if p.is_file():
            migrate_file(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
