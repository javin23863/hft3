#!/usr/bin/env python
"""Build a uniform HftBacktest-only campaign manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "packages")]

from backtest_pipeline.src.hftbacktest_only_campaign_manifest import (
    DEFAULT_REGISTRY_PATH,
    build_parameter_surface_rows,
    build_campaign_manifest_rows,
    write_parameter_surface_manifest,
    write_campaign_manifest,
)


def _load_adapter_status(path: Path | None) -> dict[str, str] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("--adapter-status-json must be an object keyed by canonical slug")
    return {str(key): str(value) for key, value in payload.items()}


def _load_parameter_sets(path: Path | None) -> list[dict[str, Any]] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, Mapping):
        payload = payload.get("parameter_sets")
    if not isinstance(payload, list):
        raise SystemExit("--parameter-sets-json must be a list or object with parameter_sets")
    parameter_sets: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise SystemExit("--parameter-sets-json entries must be objects")
        parameter_sets.append(dict(item))
    return parameter_sets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the canonical-slug HftBacktest-only campaign manifest",
    )
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument(
        "--prepared-root",
        type=Path,
        default=REPO / "data" / "hbt" / "prepared",
        help="Root containing HBT-only prepare *_manifest.json files.",
    )
    parser.add_argument("--model-registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--adapter-status-json", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--summary-out", type=Path, default=None)
    parser.add_argument(
        "--parameter-sets-json",
        type=Path,
        default=None,
        help="JSON list, or object with parameter_sets, of declared pre-HBT parameter proposals.",
    )
    parser.add_argument("--parameter-surface-out", type=Path, default=None)
    parser.add_argument("--parameter-surface-summary-out", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.parameter_sets_json is not None and args.parameter_surface_out is None:
        raise SystemExit("--parameter-sets-json requires --parameter-surface-out")
    if args.parameter_surface_out is not None and args.parameter_sets_json is None:
        raise SystemExit("--parameter-surface-out requires --parameter-sets-json")

    rows = build_campaign_manifest_rows(
        campaign_id=args.campaign_id,
        prepared_root=args.prepared_root,
        registry_path=args.model_registry,
        adapter_status_by_model=_load_adapter_status(args.adapter_status_json),
    )
    summary = write_campaign_manifest(
        rows,
        out_path=args.out,
        summary_path=args.summary_out,
    )
    output: dict[str, Any] = {"campaign_manifest": summary}
    parameter_sets = _load_parameter_sets(args.parameter_sets_json)
    if parameter_sets is not None and args.parameter_surface_out is not None:
        parameter_surface_rows = build_parameter_surface_rows(
            campaign_rows=rows,
            parameter_sets=parameter_sets,
        )
        output["parameter_surface"] = write_parameter_surface_manifest(
            parameter_surface_rows,
            out_path=args.parameter_surface_out,
            summary_path=args.parameter_surface_summary_out,
        )
    print(json.dumps(output, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
