#!/usr/bin/env python3
"""Plan a read-only RL GPU campaign budget from a feature manifest."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (_REPO_ROOT, _REPO_ROOT / "packages", _REPO_ROOT / "apps"):
    value = str(_path)
    if value not in sys.path:
        sys.path.insert(0, value)

from hft3_bootstrap import setup_repo_paths  # noqa: E402

setup_repo_paths()

from backtest_pipeline.src.hft_campaign.artifacts import write_json_atomic  # noqa: E402
from features_engine.feature_sets import MICROSTRUCTURE_FEATURE_RECEIPTS  # noqa: E402
from research_pipeline.rl_campaign_budget import plan_rl_campaign_budget  # noqa: E402
from research_pipeline.rl_training_data import FEATURE_STORE_SUPPORTED_RL_FEATURES, GPU_SUPPORTED_RL_FEATURES  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-manifest", type=Path, required=True)
    parser.add_argument("--vast-credit-usd", type=float, required=True)
    parser.add_argument("--gpu-hour-rate-usd", type=float, required=True)
    parser.add_argument("--budget-reserve-usd", type=float, default=1.0)
    parser.add_argument(
        "--measured-throughput-rows-per-gpu-hour",
        type=float,
        default=None,
        help="Measured manifest/source-row throughput from a GPU pilot.",
    )
    parser.add_argument("--measured-throughput-row-basis", default="manifest_source_rows")
    parser.add_argument("--measured-source-rows", type=float, default=None)
    parser.add_argument("--measured-duration-seconds", type=float, default=None)
    parser.add_argument("--pilot-target-rows", type=int, default=5_000_000)
    parser.add_argument("--supported-feature", action="append", default=None)
    parser.add_argument("--required-feature", action="append", default=None)
    parser.add_argument(
        "--out",
        type=Path,
        default=_REPO_ROOT / "runtime" / "reports" / "rl_campaign_budget_plan.json",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rows = _load_effective_jsonl_manifest(args.feature_manifest)
    supported = (
        _validated_supported_features(args.supported_feature)
        if args.supported_feature
        else sorted(FEATURE_STORE_SUPPORTED_RL_FEATURES)
    )
    required = args.required_feature or sorted(_registry_feature_names())
    throughput = args.measured_throughput_rows_per_gpu_hour
    if throughput is None and (
        args.measured_source_rows is not None or args.measured_duration_seconds is not None
    ):
        throughput = _derive_source_row_throughput(
            source_rows=args.measured_source_rows,
            duration_seconds=args.measured_duration_seconds,
        )
    plan = plan_rl_campaign_budget(
        feature_manifest_rows=rows,
        vast_credit_usd=args.vast_credit_usd,
        vast_gpu_hour_rate_usd=args.gpu_hour_rate_usd,
        budget_reserve_usd=args.budget_reserve_usd,
        supported_features=supported,
        required_features=required,
        measured_throughput_rows_per_gpu_hour=throughput,
        measured_throughput_row_basis=args.measured_throughput_row_basis,
        pilot_target_rows=args.pilot_target_rows,
    )
    out = args.out if args.out.is_absolute() else _REPO_ROOT / args.out
    write_json_atomic(out, plan)
    print(
        json.dumps(
            {
                "status": plan["status"],
                "out": str(out),
                "usable_gpu_hours": plan["usable_gpu_hours"],
                "unsupported_required_features": plan["unsupported_required_features"],
                "full_training_status": plan["stage_statuses"]["full_training"]["status"],
                "full_training_ready": plan["stage_statuses"]["full_training"]["status"] == "planned",
                "pilot_status": plan["stage_statuses"]["stratified_pilot"]["status"],
                "pilot_selected_rows": (
                    (plan["stage_statuses"]["stratified_pilot"]["selection"] or {}).get("selected_rows")
                ),
                "measured_throughput_row_basis": plan["measured_throughput_row_basis"],
                "estimated_full_inventory_gpu_hours": plan["estimated_full_inventory_gpu_hours"],
                "estimated_full_inventory_cost_usd": plan["estimated_full_inventory_cost_usd"],
                "estimated_full_inventory_covered": plan["estimated_full_inventory_covered"],
            },
            sort_keys=True,
        )
    )
    if plan["stage_statuses"]["full_training"]["status"] == "planned":
        return 0
    return 2 if plan["stage_statuses"]["stratified_pilot"]["status"] == "planned" else 1


def _load_effective_jsonl_manifest(path: Path) -> list[dict[str, object]]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"feature manifest not found: {path}")
    keyed_rows: dict[tuple[str, str], dict[str, object]] = {}
    unkeyed_rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if not isinstance(value, dict):
                raise ValueError(f"feature manifest row {line_no} must be an object")
            symbol = str(value.get("symbol") or "").strip()
            event_id = str(value.get("event_id") or "").strip()
            if symbol and event_id:
                keyed_rows[(symbol, event_id)] = value
            else:
                unkeyed_rows.append(value)
    return list(keyed_rows.values()) + unkeyed_rows


def _registry_feature_names() -> list[str]:
    features = MICROSTRUCTURE_FEATURE_RECEIPTS.get("features", {})
    if not isinstance(features, dict) or not features:
        raise ValueError("microstructure feature registry is empty")
    return [str(name) for name in features]


def _validated_supported_features(values: Sequence[str]) -> list[str]:
    supported = sorted({str(value).strip() for value in values if str(value).strip()})
    unsupported = sorted(set(supported) - set(GPU_SUPPORTED_RL_FEATURES))
    if unsupported:
        raise ValueError(
            "supported features must be implemented by the fs_v1 RL builder or VIX options clue builder: "
            + ", ".join(unsupported)
        )
    return supported


def _derive_source_row_throughput(*, source_rows: float | None, duration_seconds: float | None) -> float:
    if source_rows is None or duration_seconds is None:
        raise ValueError("measured source rows and duration seconds must be supplied together")
    rows = float(source_rows)
    seconds = float(duration_seconds)
    if not math.isfinite(rows) or rows <= 0.0:
        raise ValueError("measured source rows must be positive")
    if not math.isfinite(seconds) or seconds <= 0.0:
        raise ValueError("measured duration seconds must be positive")
    return rows / seconds * 3600.0


if __name__ == "__main__":
    raise SystemExit(main())
