"""CLI: validate event_universe.yaml consistency."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import yaml

from economic_event_universe.registry import (
    default_download_window,
    event_definitions,
    research_ready_types,
)
from hft3_bootstrap import data_system_root, repo_root, setup_repo_paths

_SOURCED_CALENDAR_FILES = frozenset({"bls_cpi.csv", "bls_nfp.csv", "prop_flatten.csv"})


def _registry_path() -> Path:
    return repo_root() / "packages" / "hfc3" / "events" / "event_types_registry.yaml"


def validate() -> list[str]:
    errors: list[str] = []
    defs = event_definitions()
    ready = set(research_ready_types())
    cal_dir = data_system_root() / "config" / "release_calendars"
    cal_types_sourced: set[str] = set()
    if cal_dir.is_dir():
        for path in cal_dir.glob("*.csv"):
            with path.open(newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    status = str(row.get("row_status", "") or "").upper()
                    if status and status != "SOURCED":
                        continue
                    if not status and path.name not in _SOURCED_CALENDAR_FILES:
                        continue
                    cal_types_sourced.add(str(row.get("event_type", "")))

    for et in ready:
        if et not in defs:
            errors.append(f"RESEARCH_READY type missing from event_universe.yaml: {et}")
            continue
        cfg = defs[et]
        if cfg.get("schedule") == "rule_based":
            continue
        if et not in cal_types_sourced:
            errors.append(f"RESEARCH_READY {et} has no SOURCED release_calendars row")

    for et, cfg in defs.items():
        if not cfg.get("event_context_label"):
            errors.append(f"{et}: missing event_context_label")
        if not cfg.get("official_source_url"):
            errors.append(f"{et}: missing official_source_url")

    errors.extend(_validate_events_csv())
    errors.extend(_validate_download_windows())
    errors.extend(_validate_cpp_label_artifact())
    errors.extend(_validate_registry_sync(defs))
    return errors


def _validate_download_windows() -> list[str]:
    """All catalog types must use the HFT MBO release window (-60s, +10s)."""
    expected = default_download_window()
    errs: list[str] = []
    for et, cfg in event_definitions().items():
        win = cfg.get("download_window") or {}
        start = int(win.get("start_offset_seconds", expected[0]))
        end = int(win.get("end_offset_seconds", expected[1]))
        if (start, end) != expected:
            errs.append(f"{et}: download_window ({start}, {end}) != HFT release {expected}")
    return errs


def _validate_events_csv() -> list[str]:
    path = data_system_root() / "config" / "events.csv"
    if not path.is_file():
        return ["missing events.csv"]
    ready = set(research_ready_types())
    bad: list[str] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            notes = str(row.get("notes", ""))
            if "SEED_PLACEHOLDER" in notes:
                bad.append(f"events.csv contains SEED row: {row.get('event_id')}")
            et = str(row.get("event_type", ""))
            if et not in ready:
                bad.append(f"events.csv row for non-RESEARCH_READY type: {et}")
    return bad


def _validate_cpp_label_artifact() -> list[str]:
    json_path = repo_root() / "packages" / "features_engine" / "config" / "event_context_labels.json"
    hpp_path = repo_root() / "packages" / "features_engine" / "cpp" / "include" / "event_context_labels.generated.hpp"
    regime_path = repo_root() / "packages" / "features_engine" / "cpp" / "include" / "event_context_regime.generated.hpp"
    if not json_path.is_file() or not hpp_path.is_file() or not regime_path.is_file():
        return ["missing generated label/regime artifacts; run generate_event_context_labels.py"]
    table = json.loads(json_path.read_text(encoding="utf-8"))
    defs = event_definitions()
    errs: list[str] = []
    for et, cfg in defs.items():
        row = table.get(et)
        if not row:
            errs.append(f"cpp label table missing event type: {et}")
            continue
        if row.get("label") != cfg.get("event_context_label"):
            errs.append(f"cpp/python label drift for {et}")
        if row.get("main_label") != str(cfg.get("main_context_label", "") or ""):
            errs.append(f"cpp/python main_label drift for {et}")
    errs.extend(_validate_regime_artifact())
    return errs


def _validate_regime_artifact() -> list[str]:
    json_path = repo_root() / "packages" / "features_engine" / "config" / "event_context_regime.json"
    hpp_path = repo_root() / "packages" / "features_engine" / "cpp" / "include" / "event_context_regime.generated.hpp"
    if not json_path.is_file() or not hpp_path.is_file():
        return ["missing event_context_regime.json or event_context_regime.generated.hpp"]
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    hpp = hpp_path.read_text(encoding="utf-8")
    for label in raw.get("event_shock", []):
        if f'"{label}"' not in hpp:
            return [f"regime hpp missing event_shock label: {label}"]
    for label in raw.get("prop_flatten", []):
        if f'"{label}"' not in hpp:
            return [f"regime hpp missing prop_flatten label: {label}"]
    return []


def _validate_registry_sync(defs: dict) -> list[str]:
    reg_path = _registry_path()
    if not reg_path.is_file():
        return ["missing event_types_registry.yaml"]
    raw = yaml.safe_load(reg_path.read_text(encoding="utf-8")) or {}
    reg_types = set((raw.get("event_types") or {}).keys())
    yaml_types = set(defs.keys())
    missing = yaml_types - reg_types
    if missing:
        return [f"event_types_registry.yaml missing types: {sorted(missing)[:5]}..."]
    return []


def main(argv: list[str] | None = None) -> int:
    setup_repo_paths()
    parser = argparse.ArgumentParser(prog="economic_event_universe.cli")
    parser.add_argument("command", choices=["validate", "status"], nargs="?", default="validate")
    args = parser.parse_args(argv)
    if args.command == "validate":
        errs = validate()
        if errs:
            for e in errs:
                print(e, file=sys.stderr)
            print(f"validate: FAIL ({len(errs)} issues)", file=sys.stderr)
            return 1
        print(
            f"validate: OK ({len(event_definitions())} catalog types, "
            f"{len(research_ready_types())} research-ready in events.csv)"
        )
        return 0
    if args.command == "status":
        from economic_event_universe.catalog_report import format_catalog_banner, build_macro_catalog_summary
        from hft3_bootstrap import repo_root

        print(format_catalog_banner())
        summary = build_macro_catalog_summary(repo_root())
        print(
            f"\nWindows (seed calendars, 2018-2025): {summary.catalog_window_count} | "
            f"NPZ missing slots: {summary.npz_slots_missing}"
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
