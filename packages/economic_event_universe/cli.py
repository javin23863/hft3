"""CLI: validate and build the unified economic event universe."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter

import yaml

from economic_event_universe.registry import (
    event_definitions,
    research_ready_types,
)
from economic_event_universe.service import (
    events_csv_path,
    inventory as build_inventory,
    list_calendar_rows,
    list_runnable_events,
)
from hft3_bootstrap import repo_root, setup_repo_paths


def _registry_path():
    return repo_root() / "packages" / "hfc3" / "events" / "event_types_registry.yaml"


def validate() -> list[str]:
    errors: list[str] = []
    defs = event_definitions()
    ready = set(research_ready_types())
    rows = list_calendar_rows(repo_root(), include_seed=True)
    cal_types_sourced = {str(row["event_type"]) for row in rows if row["row_status"] == "SOURCED"}

    for row in rows:
        if not row["universe_defined"]:
            errors.append(f"{row['source_file']}: event_type not in event_universe.yaml: {row['event_type']}")

    for et in ready:
        if et not in defs:
            errors.append(f"RESEARCH_READY type missing from event_universe.yaml: {et}")
            continue
        cfg = defs[et]
        if cfg.get("schedule") == "rule_based":
            continue
        if et not in cal_types_sourced:
            errors.append(f"RESEARCH_READY {et} has no SOURCED calendar row")

    for et, cfg in defs.items():
        if not cfg.get("event_context_label"):
            errors.append(f"{et}: missing event_context_label")
        if not cfg.get("official_source_url"):
            errors.append(f"{et}: missing official_source_url")

    errors.extend(_validate_events_csv())
    errors.extend(_validate_cpp_label_artifact())
    errors.extend(_validate_registry_sync(defs))
    return errors


def _validate_events_csv() -> list[str]:
    path = events_csv_path(repo_root())
    if not path.is_file():
        return ["missing events.csv"]
    known = set(event_definitions())
    bad: list[str] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            notes = str(row.get("notes", ""))
            if "SEED_PLACEHOLDER" in notes:
                bad.append(f"events.csv contains SEED row: {row.get('event_id')}")
            et = str(row.get("event_type", ""))
            if et not in known:
                bad.append(f"events.csv row for event_type not in event_universe.yaml: {et}")
            if str(row.get("row_status", "") or "SOURCED").upper() != "SOURCED":
                bad.append(f"events.csv row is not SOURCED: {row.get('event_id')}")
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


def _events_fieldnames() -> list[str]:
    return [
        "event_id",
        "event_type",
        "release_date",
        "release_time",
        "timezone",
        "window_name",
        "start_offset_seconds",
        "end_offset_seconds",
        "symbols",
        "priority",
        "source",
        "source_url",
        "effective_date",
        "notes",
        "row_status",
    ]


def build_events_csv(*, dry_run: bool = False, root=None) -> int:
    root = root or repo_root()
    path = events_csv_path(root)
    existing = _load_existing(path)
    merged = {
        eid: row
        for eid, row in existing.items()
        if "SEED_PLACEHOLDER" not in str(row.get("notes", ""))
    }
    generated = list_runnable_events(root)
    added = updated = 0
    for row in generated:
        eid = row["event_id"]
        out = {key: str(row.get(key, "")) for key in _events_fieldnames()}
        if eid not in merged:
            added += 1
        elif {key: str(merged[eid].get(key, "")) for key in _events_fieldnames()} != out:
            updated += 1
        merged[eid] = out
    rows = [merged[k] for k in sorted(merged.keys())]
    counts = Counter(r["event_type"] for r in rows)
    if dry_run:
        print(f"dry-run: {len(rows)} total rows ({added} added, {updated} updated)")
        for et, n in sorted(counts.items()):
            print(f"  {et}: {n}")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_events_fieldnames(), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {path}")
    for et, n in sorted(counts.items()):
        print(f"  {et}: {n}")
    return 0


def _load_existing(path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {row["event_id"]: row for row in csv.DictReader(f)}


def print_inventory(*, json_out: bool = False, root=None) -> int:
    data = build_inventory(root or repo_root())
    if json_out:
        print(json.dumps(data, indent=2))
        return 0
    print(f"canonical_config_root: {data['canonical_config_root']}")
    print(f"generated_events_csv: {data['generated_events_csv']}")
    print(f"event_type_count: {data['event_type_count']}")
    print(f"calendar_row_count: {data['calendar_row_count']}")
    print(f"runnable_event_count: {data['runnable_event_count']}")
    print("row_status_counts:")
    for status, count in data["row_status_counts"].items():
        print(f"  {status}: {count}")
    print("event_types:")
    for row in data["event_types"]:
        print(
            "  "
            f"{row['event_type']}: status={row['status']} "
            f"calendar_rows={row['calendar_row_count']} "
            f"runnable_rows={row['runnable_row_count']} "
            f"dates={row['first_release_date']}..{row['last_release_date']}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_repo_paths()
    parser = argparse.ArgumentParser(prog="economic_event_universe.cli")
    parser.add_argument("command", choices=["validate", "build-events", "inventory"], nargs="?", default="validate")
    parser.add_argument("--dry-run", action="store_true", help="For build-events, print counts without writing")
    parser.add_argument("--json", action="store_true", help="For inventory, emit JSON")
    args = parser.parse_args(argv)
    if args.command == "validate":
        errs = validate()
        if errs:
            for e in errs:
                print(e, file=sys.stderr)
            print(f"validate: FAIL ({len(errs)} issues)", file=sys.stderr)
            return 1
        print(f"validate: OK ({len(event_definitions())} catalog types, {len(research_ready_types())} research-ready)")
        return 0
    if args.command == "build-events":
        return build_events_csv(dry_run=args.dry_run, root=repo_root())
    if args.command == "inventory":
        return print_inventory(json_out=args.json, root=repo_root())
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
