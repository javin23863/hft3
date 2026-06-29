#!/usr/bin/env python
"""Build a uniform HftBacktest-only campaign manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "packages")]

from backtest_pipeline.src.hftbacktest_only_campaign_manifest import (
    DEFAULT_CHECKPOINT_EVERY_ROWS,
    DEFAULT_REGISTRY_PATH,
    HftBacktestOnlyCampaignManifestError,
    iter_parameter_surface_rows,
    normalize_self_learning_parameter_sets_payload,
    stream_campaign_manifest,
    stream_first_eligible_canary_manifest,
    stream_parameter_surface_manifest,
)


DEFAULT_PARAMETER_SETS = REPO / "runtime" / "hbt" / "hbt_parameter_sets.json"


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
    try:
        return normalize_self_learning_parameter_sets_payload(payload)
    except HftBacktestOnlyCampaignManifestError as exc:
        raise SystemExit(f"--parameter-sets-json must be a self-learning export: {exc}") from exc


def _parameter_surface_config_status(path: Path) -> str:
    if not path.is_file():
        return "pipeline_blocker:parameter_sets_config_missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        normalize_self_learning_parameter_sets_payload(payload)
    except (OSError, json.JSONDecodeError, HftBacktestOnlyCampaignManifestError) as exc:
        return f"pipeline_blocker:parameter_sets_config_invalid:{exc}"
    return "parameter_config_present"


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
    parser.add_argument("--checkpoint-out", type=Path, default=None)
    parser.add_argument(
        "--checkpoint-every-rows",
        type=int,
        default=DEFAULT_CHECKPOINT_EVERY_ROWS,
        help="Flush and checkpoint streaming manifest progress every N rows.",
    )
    parser.add_argument(
        "--parameter-sets-json",
        type=Path,
        default=None,
        help=(
            "JSON self-learning export envelope of declared pre-HBT parameter proposals "
            "from the existing autoresearch/self-learning loop."
        ),
    )
    parser.add_argument("--parameter-surface-out", type=Path, default=None)
    parser.add_argument("--parameter-surface-summary-out", type=Path, default=None)
    parser.add_argument("--parameter-surface-checkpoint-out", type=Path, default=None)
    parser.add_argument("--canary-out", type=Path, default=None)
    parser.add_argument("--canary-count", type=int, default=None)
    parser.add_argument("--canary-summary-out", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.parameter_sets_json is not None and args.parameter_surface_out is None:
        raise SystemExit("--parameter-sets-json requires --parameter-surface-out")
    if args.parameter_surface_out is not None and args.parameter_sets_json is None:
        raise SystemExit("--parameter-surface-out requires --parameter-sets-json")
    if args.canary_out is not None and args.canary_count is None:
        raise SystemExit("--canary-out requires --canary-count")
    if args.canary_count is not None and args.canary_out is None:
        raise SystemExit("--canary-count requires --canary-out")

    declared_parameter_sets_path = args.parameter_sets_json or DEFAULT_PARAMETER_SETS
    parameter_surface_config_status = _parameter_surface_config_status(
        declared_parameter_sets_path
    )

    summary = stream_campaign_manifest(
        campaign_id=args.campaign_id,
        prepared_root=args.prepared_root,
        out_path=args.out,
        registry_path=args.model_registry,
        adapter_status_by_model=_load_adapter_status(args.adapter_status_json),
        summary_path=args.summary_out,
        checkpoint_path=args.checkpoint_out,
        checkpoint_every_rows=args.checkpoint_every_rows,
        parameter_surface_status="base_only",
        parameter_surface_config_status=parameter_surface_config_status,
        parameter_sets_json=declared_parameter_sets_path,
    )
    output: dict[str, Any] = {"campaign_manifest": summary}
    parameter_sets = _load_parameter_sets(args.parameter_sets_json)
    canary_source = args.out
    canary_parameter_surface_status = summary["parameter_surface_status"]
    canary_parameter_surface_config_status = summary["parameter_surface_config_status"]
    if parameter_sets is not None and args.parameter_surface_out is not None:
        output["parameter_surface"] = stream_parameter_surface_manifest(
            iter_parameter_surface_rows(
                campaign_rows=_iter_jsonl(args.out),
                parameter_sets=parameter_sets,
            ),
            out_path=args.parameter_surface_out,
            summary_path=args.parameter_surface_summary_out,
            checkpoint_path=args.parameter_surface_checkpoint_out,
            checkpoint_every_rows=args.checkpoint_every_rows,
        )
        canary_source = args.parameter_surface_out
        canary_parameter_surface_status = "parameter_surface_expanded"
        canary_parameter_surface_config_status = "parameter_config_present"
    if args.canary_out is not None and args.canary_count is not None:
        output["canary_manifest"] = stream_first_eligible_canary_manifest(
            _iter_jsonl(canary_source),
            out_path=args.canary_out,
            count=args.canary_count,
            summary_path=args.canary_summary_out,
            source_manifest=canary_source,
            parameter_surface_status=canary_parameter_surface_status,
            parameter_surface_config_status=canary_parameter_surface_config_status,
        )
    print(json.dumps(output, indent=2, sort_keys=True, default=str))
    return 0


def _iter_jsonl(path: Path):
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


if __name__ == "__main__":
    raise SystemExit(main())
