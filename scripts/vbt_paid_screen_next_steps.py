#!/usr/bin/env python3
"""Print current VectorBT paid-screen phase and exact next commands."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

_DEFAULT_GATE = _REPO / "runtime" / "reports" / "paid_screen_ready_gate.json"
_DEFAULT_DECL = _REPO / "runtime" / "reports" / "vbt_full_run_declaration.json"
_DEFAULT_FULL_UNITS = _REPO / "runtime" / "reports" / "vbt_full_units.jsonl"


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest_ok(manifest: Dict[str, Any]) -> bool:
    try:
        e = int(manifest.get("expected_work_units") or 0)
        c = int(manifest.get("completed_work_units") or 0)
        f = int(manifest.get("failed_work_units") or 0)
        s = int(manifest.get("skipped_work_units") or 0)
        return e > 0 and c + f + s == e and f == 0
    except (TypeError, ValueError):
        return False


def _find_latest_manifest(pattern: str) -> Optional[Path]:
    base = _REPO / "research_cards" / "pipeline_runs"
    if not base.is_dir():
        return None
    candidates: List[Path] = []
    for child in base.iterdir():
        if not child.is_dir():
            continue
        if pattern in child.name:
            manifest = child / "paid_screen_run_manifest.json"
            if manifest.is_file():
                candidates.append(manifest)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _resolve_paths(args: argparse.Namespace) -> Dict[str, Optional[Path]]:
    pilot = Path(args.pilot_artifact) if args.pilot_artifact else None
    if pilot is None:
        env = os.environ.get("VBT_PILOT_ARTIFACT")
        pilot = Path(env) if env else None
    smoke = Path(args.smoke_manifest) if args.smoke_manifest else None
    if smoke is None:
        env = os.environ.get("VBT_SMOKE_MANIFEST")
        smoke = Path(env) if env else _find_latest_manifest("paid_smoke_")
    gate = Path(args.gate_file) if args.gate_file else _DEFAULT_GATE
    full = Path(args.full_manifest) if args.full_manifest else None
    if full is None:
        env = os.environ.get("VBT_FULL_MANIFEST")
        full = Path(env) if env else _find_latest_manifest("paid_full_")
    return {
        "pilot": pilot,
        "smoke": smoke,
        "gate": gate,
        "full": full,
        "full_units": Path(args.full_units_jsonl) if args.full_units_jsonl else _DEFAULT_FULL_UNITS,
        "decl": _DEFAULT_DECL,
    }


def _declaration_commands() -> List[str]:
    return [
        "Phase D0 — full-run declaration (required before Vast rent)",
        "See docs/project/VBT_PAID_SCREEN_POST_GATE_PLAYBOOK.md § D0",
        "Write runtime/reports/vbt_full_run_declaration.json (on Vast after unit generation)",
        "Required fields: expected_work_units, stall_minutes, abort_on_failed_units, git_head, pilot_hashes",
        "Use POST_GATE_PLAYBOOK D0 python template to emit the declaration JSON",
    ]


def _phase(paths: Dict[str, Optional[Path]]) -> Tuple[str, List[str]]:
    pilot = paths["pilot"]
    smoke = paths["smoke"]
    gate = paths["gate"]
    full = paths["full"]

    if pilot is None or not pilot.is_file():
        return "A", [
            "Phase A — pilot (1 unit, local, no rent)",
            "export HFT3_REPO=\"$(pwd)\"",
            "bash scripts/install_vbt_hbt_handoff_verify_deps.sh",
            "python scripts/run_pipeline.py \\",
            "  --thesis \"Fade spread blowout after CPI surprise on MES using HYP_5\" \\",
            "  --event-id CPI_2024_09_11_TIGHT \\",
            "  --vectorbt --vectorbt-scope pilot --no-llm",
            "export VBT_PILOT_ARTIFACT=\"$HFT3_REPO/research_cards/pipeline_runs/<run_id>/screening_artifact.json\"",
        ]

    smoke_m = _load_json(smoke) if smoke and smoke.is_file() else None
    if smoke_m is None or not _manifest_ok(smoke_m):
        return "B", [
            "Phase B — smoke batch (8–16 units, workers 4, no full rent)",
            "bash scripts/run_vbt_paid_screen_smoke.sh",
            "export VBT_SMOKE_MANIFEST=\"<path>/paid_screen_run_manifest.json\"",
            "Require: failed_work_units == 0",
        ]

    gate_p = _load_json(gate) if gate else None
    if gate_p is None or not gate_p.get("ready_for_full_run"):
        return "C", [
            "Phase C — ready gate (blocks Vast rent)",
            f"python scripts/validate_paid_screen_ready_gate.py \\",
            f"  --pilot-artifact \"{pilot}\" \\",
            f"  --smoke-manifest \"{smoke}\" \\",
            f"  --out runtime/reports/paid_screen_ready_gate.json",
            "Proceed only if exit 0 and ready_for_full_run: true",
        ]

    decl_missing = not paths["decl"].is_file()

    full_m = _load_json(full) if full and full.is_file() else None
    if full_m is None:
        commands = [
            "Phase D1–D4 — Vast full VectorBT screen (v2 default; units generated on host)",
            "See docs/project/VBT_PAID_SCREEN_POST_GATE_PLAYBOOK.md and docs/project/PAID_SCREEN_OPS_COMMANDS.md",
            "Gate ready. Sync repo + NPZ + paid_screen_ready_gate.json to Vast; then on Vast host:",
            "bash scripts/run_vbt_paid_screen_vast_full.sh  # uses run_paid_screen.py --execution-mode v2",
            "Rollback only: export VBT_EXECUTION_MODE=v1 before the same script",
            "v2 resume: export VBT_RESUME=1; cache/recycle: VBT_CACHE_MEMORY_LIMIT_MB, VBT_MAX_BATCHES_BEFORE_RECYCLE",
            "Units: events.csv TIGHT rows × CME M6 symbols × active model registry (not local Stage A survivors).",
            "Env knobs: VBT_WORKERS | VBT_MODEL_SCOPE=active | VBT_MODEL_IDS=... | VBT_EVENT_TYPES=... | VBT_SYMBOLS=...",
            "export VBT_FULL_RUN_ID=\"paid_full_$(date -u +%Y%m%dT%H%M%SZ)\"  # optional override",
        ]
        if decl_missing:
            commands = _declaration_commands() + commands
        return "D1-D4", commands

    if full_m.get("status") != "complete" or not _manifest_ok(full_m):
        return "D5", [
            "Phase D5 — monitor or recover partial full run",
            f"Manifest: {full}",
            "python scripts/validate_paid_screen_ready_gate.py \\",
            f"  --watch-manifest \"{full}\" --stall-minutes 30",
            "If failed_work_units > 0: abort, sync partial, do not cockpit GREEN",
        ]

    return "E", [
        "Phase E — backtest screen complete; validate + aggregate promoted",
        f"export VBT_FULL_MANIFEST=\"{full}\"",
        "python scripts/aggregate_vbt_promoted_ids.py \\",
        f"  --manifest \"{full}\" \\",
        "  --out runtime/reports/vbt_full_promoted_ids.json",
        "Optional: HBT realism on promoted_ids only (not full universe)",
        "Docs: docs/project/VBT_PAID_SCREEN_POST_GATE_PLAYBOOK.md § E1–E4",
    ]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="VectorBT paid screen next steps")
    parser.add_argument("--pilot-artifact", default=None)
    parser.add_argument("--smoke-manifest", default=None)
    parser.add_argument("--gate-file", default=None)
    parser.add_argument("--full-manifest", default=None)
    parser.add_argument("--full-units-jsonl", default=None)
    parser.add_argument("--json", action="store_true", help="Emit phase as JSON")
    args = parser.parse_args(argv)

    paths = _resolve_paths(args)
    phase, commands = _phase(paths)

    if args.json:
        print(json.dumps({"phase": phase, "commands": commands, "paths": {k: str(v) if v else None for k, v in paths.items()}}))
    else:
        print(f"CURRENT_PHASE: {phase}")
        for line in commands:
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
