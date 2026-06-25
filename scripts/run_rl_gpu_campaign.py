#!/usr/bin/env python3
"""Run a full research-only RL GPU campaign from fs_v1 feature stores."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (_REPO_ROOT, _REPO_ROOT / "packages", _REPO_ROOT / "apps"):
    value = str(_path)
    if value not in sys.path:
        sys.path.insert(0, value)

from hft3_bootstrap import setup_repo_paths  # noqa: E402

setup_repo_paths()

from backtest_pipeline.src.hft_campaign._hashing import sha256_file  # noqa: E402
from backtest_pipeline.src.hft_campaign.artifacts import write_json_atomic  # noqa: E402
from data_system.src.feature_store import feature_store_root, load_manifest  # noqa: E402
from research_pipeline.rl_agents import (  # noqa: E402
    train_deep_rl_policy_artifact,
    write_rl_deep_policy_artifact,
)
from research_pipeline.rl_campaign_budget import plan_rl_campaign_budget  # noqa: E402
from research_pipeline.rl_training_data import (  # noqa: E402
    FEATURE_STORE_SUPPORTED_RL_FEATURES,
    build_rl_training_data,
)

RL_GPU_CAMPAIGN_SCHEMA_VERSION = "hft3_rl_gpu_campaign_v1"
DEFAULT_RL_CAMPAIGN_FEATURES = [
    "order_book_imbalance",
    "queue_imbalance",
    "order_flow_imbalance",
    "micro_price",
    "spread",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT)
    parser.add_argument("--feature-store-root", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--budget-plan", type=Path, required=True)
    parser.add_argument("--budget-plan-sha256", required=True)
    parser.add_argument("--host-kind", choices=["vastai"], required=True)
    parser.add_argument("--gpu-host", required=True)
    parser.add_argument("--expected-duration-minutes", type=float, required=True)
    parser.add_argument("--stop-rule", required=True)
    parser.add_argument("--operator-approval", required=True)
    parser.add_argument("--allow-pre-ppo-proxy", action="store_true")
    parser.add_argument("--symbol", action="append", default=None)
    parser.add_argument("--feature", action="append", default=None)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cuda")
    parser.add_argument("--build-workers", type=int, default=0)
    parser.add_argument("--reward-horizon-rows", type=int, default=1)
    parser.add_argument("--reward-horizon-ns", type=int, default=None)
    parser.add_argument("--feature-latency-ms", type=float, default=1.0)
    parser.add_argument("--spread-cost-multiplier", type=float, default=0.05)
    parser.add_argument("--steps", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=4096)
    parser.add_argument("--hidden-dim", type=int, default=64)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--eval-fraction", type=float, default=0.2)
    parser.add_argument("--max-rows", type=int, default=50_000_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--resume-checkpoint", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    repo_root = Path(args.repo_root)
    root = Path(args.feature_store_root) if args.feature_store_root else feature_store_root(repo_root)
    output_root = Path(args.output_root)
    features = _validated_features(args.feature)
    budget_plan = _load_budget_plan(args.budget_plan)
    budget_plan_sha256 = _require_budget_plan_sha(args.budget_plan, args.budget_plan_sha256)
    launch_gate = _require_launch_gate(args=args, argv=argv, budget_plan_sha256=budget_plan_sha256)
    manifest = load_manifest(root)
    _require_budget_ready(budget_plan, features, manifest)
    grouped = _event_ids_by_symbol(manifest, selected_symbols=args.symbol)
    workers = _build_worker_count(requested=args.build_workers, symbol_count=len(grouped))

    output_root.mkdir(parents=True, exist_ok=True)
    build_results = _run_builds(
        repo_root=repo_root,
        feature_store_root=root,
        output_root=output_root,
        grouped_event_ids=grouped,
        feature_names=features,
        workers=workers,
        reward_horizon_rows=args.reward_horizon_rows,
        reward_horizon_ns=args.reward_horizon_ns,
        feature_latency_ms=args.feature_latency_ms,
        spread_cost_multiplier=args.spread_cost_multiplier,
    )
    runs = []
    for build in sorted(build_results, key=lambda item: str(item["symbol"])):
        run = dict(build)
        if build.get("build_status") == "built_research_only":
            run["train"] = _train_symbol_safe(
                rows_path=Path(str(build["rows_path"])),
                output_dir=Path(str(build["symbol_output_dir"])) / "deep_policy",
                feature_names=features,
                device=args.device,
                seed=args.seed,
                max_rows=args.max_rows,
                steps=args.steps,
                batch_size=args.batch_size,
                hidden_dim=args.hidden_dim,
                learning_rate=args.learning_rate,
                eval_fraction=args.eval_fraction,
                resume_checkpoint=args.resume_checkpoint,
            )
        else:
            run["train"] = {
                "status": "skipped_build_failed",
                "max_rows": args.max_rows,
                "budget_exhausted": False,
            }
        runs.append(run)

    failure_count = sum(
        1
        for run in runs
        if run.get("build_status") != "built_research_only"
        or run.get("train", {}).get("status") != "trained_research_only"
    )
    training_budget = _campaign_training_budget_receipt(runs, max_rows=args.max_rows)
    status = _campaign_status(
        failure_count=failure_count,
        budget_exhausted=bool(training_budget["budget_exhausted"]),
    )
    summary = {
        "schema_version": RL_GPU_CAMPAIGN_SCHEMA_VERSION,
        "status": status,
        "created_at_utc": started_at,
        "repo_root": str(repo_root),
        "git_commit": _git_commit(repo_root),
        "runner_sha256": sha256_file(Path(__file__)),
        "feature_store_root": str(root),
        "output_root": str(output_root),
        "feature_names": features,
        "symbols": sorted(grouped),
        "build_workers": workers,
        "device": args.device,
        "max_rows": args.max_rows,
        "budget_exhausted": training_budget["budget_exhausted"],
        "any_symbol_capped": training_budget["any_symbol_capped"],
        "capped_symbols": training_budget["capped_symbols"],
        "capped_symbol_count": training_budget["capped_symbol_count"],
        "training_budget": training_budget,
        "launch_gate": launch_gate,
        "algorithm_status": "pre_ppo_deep_q_proxy_not_rl5_completion",
        "budget_plan": _budget_plan_receipt(args.budget_plan, budget_plan),
        "promotion_status": "blocked_downstream_validation_required",
        "promotable": False,
        "runs": runs,
        "failure_count": failure_count,
        "duration_seconds": round(time.perf_counter() - started, 6),
        "decision_time_boundary": (
            "research-only full supported-feature deep-Q proxy campaign; artifacts remain "
            "blocked until PPO roadmap work, VectorBT, robustness evidence, and "
            "HftBacktest validation pass"
        ),
    }
    write_json_atomic(output_root / "rl_gpu_campaign_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if status == "completed" else 2


def _event_ids_by_symbol(
    manifest: Mapping[Any, Mapping[str, Any]],
    *,
    selected_symbols: Sequence[str] | None,
) -> dict[str, list[str]]:
    selected = {str(symbol).strip() for symbol in selected_symbols or [] if str(symbol).strip()}
    grouped: dict[str, set[str]] = {}
    for key, record in manifest.items():
        symbol = ""
        event_id = ""
        if isinstance(key, tuple) and len(key) >= 2:
            symbol = str(key[0])
            event_id = str(key[1])
        if isinstance(record, Mapping):
            symbol = str(record.get("symbol") or symbol)
            event_id = str(record.get("event_id") or event_id)
        symbol = symbol.strip()
        event_id = event_id.strip()
        if not symbol or not event_id:
            continue
        if selected and symbol not in selected:
            continue
        grouped.setdefault(symbol, set()).add(event_id)
    if selected:
        missing = sorted(selected - set(grouped))
        if missing:
            raise ValueError("selected symbols not found in feature manifest: " + ", ".join(missing))
    if not grouped:
        raise ValueError("feature manifest contains no symbol/event rows")
    return {symbol: sorted(events) for symbol, events in sorted(grouped.items())}


def _build_worker_count(*, requested: int, symbol_count: int) -> int:
    if symbol_count <= 0:
        return 0
    if requested > 0:
        return max(1, min(int(requested), symbol_count))
    cpu_count = os.cpu_count() or 1
    return max(1, min(symbol_count, int(cpu_count * 0.85) or 1))


def _run_builds(
    *,
    repo_root: Path,
    feature_store_root: Path,
    output_root: Path,
    grouped_event_ids: Mapping[str, Sequence[str]],
    feature_names: Sequence[str],
    workers: int,
    reward_horizon_rows: int,
    reward_horizon_ns: int | None,
    feature_latency_ms: float,
    spread_cost_multiplier: float,
) -> list[dict[str, Any]]:
    tasks = [
        {
            "repo_root": str(repo_root),
            "feature_store_root": str(feature_store_root),
            "symbol": symbol,
            "event_ids": list(event_ids),
            "feature_names": list(feature_names),
            "output_dir": str(output_root / _safe_symbol_dir(symbol) / "rows"),
            "reward_horizon_rows": reward_horizon_rows,
            "reward_horizon_ns": reward_horizon_ns,
            "feature_latency_ms": feature_latency_ms,
            "spread_cost_multiplier": spread_cost_multiplier,
        }
        for symbol, event_ids in grouped_event_ids.items()
    ]
    if workers <= 1:
        return [_build_symbol_rows(task) for task in tasks]
    results = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_build_symbol_rows, task): task for task in tasks}
        for future in as_completed(futures):
            results.append(future.result())
    return results


def _build_symbol_rows(task: Mapping[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    symbol = str(task["symbol"])
    output_dir = Path(str(task["output_dir"]))
    symbol_output_dir = output_dir.parent
    try:
        result = build_rl_training_data(
            repo_root=Path(str(task["repo_root"])),
            feature_store_root=Path(str(task["feature_store_root"])),
            symbol=symbol,
            event_ids=list(task["event_ids"]),
            feature_names=list(task["feature_names"]),
            output_dir=output_dir,
            reward_horizon_rows=int(task["reward_horizon_rows"]),
            reward_horizon_ns=task["reward_horizon_ns"],
            feature_latency_ms=float(task["feature_latency_ms"]),
            spread_cost_multiplier=float(task["spread_cost_multiplier"]),
            max_rows=None,
        )
        manifest = result.manifest
        return {
            "symbol": symbol,
            "event_count": len(task["event_ids"]),
            "symbol_output_dir": str(symbol_output_dir),
            "rows_path": str(result.rows_path),
            "manifest_path": str(result.manifest_path),
            "build_status": manifest.get("status"),
            "row_count": manifest.get("row_count"),
            "rows_sha256": manifest.get("rows_sha256"),
            "build_duration_seconds": round(time.perf_counter() - started, 6),
        }
    except Exception as exc:
        return {
            "symbol": symbol,
            "event_count": len(task["event_ids"]),
            "symbol_output_dir": str(symbol_output_dir),
            "build_status": "failed",
            "failure_reasons": [str(exc)],
            "build_duration_seconds": round(time.perf_counter() - started, 6),
        }


def _train_symbol(
    *,
    rows_path: Path,
    output_dir: Path,
    feature_names: Sequence[str],
    device: str,
    seed: int,
    max_rows: int,
    steps: int,
    batch_size: int,
    hidden_dim: int,
    learning_rate: float,
    eval_fraction: float,
    resume_checkpoint: Path | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    artifact = train_deep_rl_policy_artifact(
        training_data_path=rows_path,
        feature_names=feature_names,
        output_dir=output_dir,
        device=device,
        seed=seed,
        max_rows=max_rows,
        steps=steps,
        batch_size=batch_size,
        hidden_dim=hidden_dim,
        learning_rate=learning_rate,
        eval_fraction=eval_fraction,
        resume_checkpoint=resume_checkpoint,
    )
    write_rl_deep_policy_artifact(output_dir / "deep_rl_policy_artifact.json", artifact)
    training_summary = artifact.get("training_summary") or {}
    training_budget = training_summary.get("training_budget") or {}
    return {
        "status": artifact.get("status"),
        "failure_reasons": artifact.get("failure_reasons", []),
        "artifact_path": str(output_dir / "deep_rl_policy_artifact.json"),
        "checkpoint": artifact.get("checkpoint", {}),
        "row_count": training_summary.get("row_count"),
        "source_row_count": training_summary.get("source_row_count"),
        "max_rows": training_summary.get("max_rows", max_rows),
        "budget_exhausted": bool(training_budget.get("budget_exhausted", False)),
        "eval_mse": training_summary.get("eval_mse"),
        "train_duration_seconds": round(time.perf_counter() - started, 6),
    }


def _validated_features(values: Sequence[str] | None) -> list[str]:
    features = [str(value).strip() for value in (values or DEFAULT_RL_CAMPAIGN_FEATURES)]
    features = [feature for feature in features if feature]
    unsupported = sorted(set(features) - set(FEATURE_STORE_SUPPORTED_RL_FEATURES))
    if unsupported:
        raise ValueError(
            "features must be implemented by the fs_v1 RL builder: "
            + ", ".join(unsupported)
        )
    if not features:
        raise ValueError("at least one RL feature is required")
    return features


def _load_budget_plan(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("budget plan must be a JSON object")
    return payload


def _require_budget_plan_sha(path: Path, expected_sha256: str) -> str:
    expected = str(expected_sha256 or "").strip().lower()
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise ValueError("budget plan sha256 must be a 64-character lowercase or uppercase hex digest")
    actual = sha256_file(Path(path)).lower()
    if actual != expected:
        raise ValueError("budget plan sha256 does not match reviewed launch input")
    return actual


def _require_launch_gate(
    *,
    args: argparse.Namespace,
    argv: Sequence[str] | None,
    budget_plan_sha256: str,
    current_os_name: str | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    gpu_host = str(getattr(args, "gpu_host", "") or "").strip()
    host_kind = str(getattr(args, "host_kind", "") or "").strip().lower()
    stop_rule = str(getattr(args, "stop_rule", "") or "").strip()
    approval = str(getattr(args, "operator_approval", "") or "").strip()
    os_name = current_os_name or os.name

    if host_kind != "vastai":
        blockers.append("host_kind_must_be_vastai")
    if not gpu_host:
        blockers.append("gpu_host_missing")
    if str(getattr(args, "device", "")).strip() != "cuda":
        blockers.append("vastai_paid_campaign_requires_cuda")
    if os_name == "nt":
        blockers.append("vastai_campaign_refuses_windows_msi_host")
    try:
        expected_minutes = float(getattr(args, "expected_duration_minutes"))
        if not expected_minutes > 0.0:
            blockers.append("expected_duration_minutes_must_be_positive")
    except (TypeError, ValueError):
        expected_minutes = 0.0
        blockers.append("expected_duration_minutes_must_be_positive")
    if not stop_rule:
        blockers.append("stop_rule_missing")
    if approval != "approved-vastai-paid-rl-campaign":
        blockers.append("operator_approval_token_missing")
    if not bool(getattr(args, "allow_pre_ppo_proxy", False)):
        blockers.append("pre_ppo_proxy_ack_missing")

    command = _command_line(argv)
    gate = {
        "schema_version": "hft3_rl_gpu_campaign_launch_gate_v1",
        "status": "ready_for_paid_gpu_campaign" if not blockers else "blocked",
        "failure_reasons": blockers,
        "host_kind": host_kind,
        "gpu_host": gpu_host,
        "device": str(getattr(args, "device", "")),
        "expected_duration_minutes": expected_minutes,
        "stop_rule": stop_rule,
        "budget_plan_sha256": budget_plan_sha256,
        "operator_approval": approval == "approved-vastai-paid-rl-campaign",
        "local_os_name": os_name,
        "command": command,
        "algorithm": "offline_deep_q_proxy_mse",
        "ppo_status": "deferred; this campaign is not RL-5 PPO completion",
        "decision_time_boundary": (
            "launch gate only; paid GPU work is allowed only on VastAI with an "
            "approved, bounded, non-local research-only command"
        ),
    }
    if blockers:
        raise ValueError("rl gpu campaign launch gate blocked: " + ", ".join(blockers))
    return gate


def _command_line(argv: Sequence[str] | None) -> str:
    if argv is None:
        return " ".join(sys.argv)
    return " ".join(["run_rl_gpu_campaign.py", *[str(value) for value in argv]])


def _require_budget_ready(
    plan: Mapping[str, Any],
    features: Sequence[str],
    manifest: Mapping[Any, Mapping[str, Any]],
) -> None:
    if plan.get("status") != "full_training_plan_ready":
        raise ValueError("budget plan is not full_training_plan_ready")
    if set(plan.get("required_features") or []) != set(features):
        raise ValueError("budget plan required_features do not match campaign features")
    if plan.get("measured_throughput_row_basis") != "manifest_source_rows":
        raise ValueError("budget plan throughput row basis is not manifest_source_rows")
    actual = plan_rl_campaign_budget(
        feature_manifest_rows=manifest,
        vast_credit_usd=float((plan.get("vast_budget") or {}).get("credit_usd", 0.0)),
        vast_gpu_hour_rate_usd=float((plan.get("vast_budget") or {}).get("gpu_hour_rate_usd", 1.0)),
        budget_reserve_usd=float((plan.get("vast_budget") or {}).get("reserve_usd", 0.0)),
        supported_features=features,
        required_features=features,
        measured_throughput_rows_per_gpu_hour=float(plan.get("measured_throughput_rows_per_gpu_hour") or 1.0),
        measured_throughput_row_basis="manifest_source_rows",
        pilot_target_rows=1,
    )
    if actual.get("known_inventory_rows") != plan.get("known_inventory_rows"):
        raise ValueError("budget plan known_inventory_rows does not match feature manifest")
    if actual.get("manifest_source_row_fingerprint") != plan.get("manifest_source_row_fingerprint"):
        raise ValueError("budget plan manifest_source_row_fingerprint does not match feature manifest")
    if actual.get("status") != "full_training_plan_ready":
        raise ValueError("current feature manifest is not full-training budget-ready")


def _budget_plan_receipt(path: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": plan.get("status"),
        "path": str(path),
        "sha256": sha256_file(Path(path)),
        "known_inventory_rows": plan.get("known_inventory_rows"),
        "manifest_source_row_fingerprint": plan.get("manifest_source_row_fingerprint"),
        "estimated_full_inventory_gpu_hours": plan.get("estimated_full_inventory_gpu_hours"),
        "estimated_full_inventory_cost_usd": plan.get("estimated_full_inventory_cost_usd"),
    }


def _campaign_training_budget_receipt(
    runs: Sequence[Mapping[str, Any]],
    *,
    max_rows: int,
) -> dict[str, Any]:
    capped_symbols = sorted(
        str(run.get("symbol") or "")
        for run in runs
        if isinstance(run.get("train"), Mapping)
        and bool(run["train"].get("budget_exhausted"))
    )
    capped_symbols = [symbol for symbol in capped_symbols if symbol]
    return {
        "max_rows": int(max_rows),
        "budget_exhausted": bool(capped_symbols),
        "any_symbol_capped": bool(capped_symbols),
        "capped_symbol_count": len(capped_symbols),
        "capped_symbols": capped_symbols,
    }


def _campaign_status(*, failure_count: int, budget_exhausted: bool) -> str:
    if failure_count:
        return "partial"
    if budget_exhausted:
        return "partial_budget_exhausted"
    return "completed"


def _train_symbol_safe(**kwargs: Any) -> dict[str, Any]:
    try:
        return _train_symbol(**kwargs)
    except Exception as exc:
        return {
            "status": "failed",
            "failure_reasons": [str(exc)],
            "max_rows": kwargs.get("max_rows"),
            "budget_exhausted": False,
            "train_duration_seconds": 0.0,
        }


def _safe_symbol_dir(symbol: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in symbol).strip("_") or "UNKNOWN"


def _git_commit(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return ""
    return result.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
