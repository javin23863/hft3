#!/usr/bin/env python
"""Run every row in a HftBacktest-only campaign manifest.

Rows that are not admissible write blocker receipts and remain part of the
campaign. Only rows with no pre-HBT blocker call the active HFTBacktest runner.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import multiprocessing as mp
import sys
from pathlib import Path
from typing import Any, Iterator, Mapping

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "packages")]

from backtest_pipeline.src.hftbacktest_only_io import safe_stem, write_json_atomic
from backtest_pipeline.src.hftbacktest_only_pipeline import (  # noqa: E402
    HBT_DATA_CONTRACT_VERSION,
    HftBacktestOnlyRunConfig,
    HYPOTHESIS_LIMIT_ORDER_SURFACE_VERSION,
    run_hftbacktest_only,
)


SCHEMA_VERSION = "hft3_hftbacktest_only_campaign_run_summary_v1"
ROW_RESULT_SCHEMA_VERSION = "hft3_hftbacktest_only_campaign_row_result_v1"
PRODUCT_METADATA_POLICY = "explicit_per_symbol_contract_tick_lot_contract_required"
MODEL_SPECIFIC_STRATEGY_ID = "hypothesis_limit_order"
MODEL_SPECIFIC_STRATEGY_SURFACE_VERSION = HYPOTHESIS_LIMIT_ORDER_SURFACE_VERSION


def run_campaign(
    *,
    manifest_path: Path,
    out_root: Path,
    strategy_id: str = "hypothesis_limit_order",
    dry_run: bool = False,
    workers: int = 1,
    max_tasks_per_child: int = 0,
    resume: bool = False,
    maker_fee: float | None = None,
    taker_fee: float | None = None,
    required_feature_backend: str = "cpp",
    latency_model: str = "chi404_measured",
    entry_latency_ns: int | None = None,
    response_latency_ns: int | None = None,
) -> dict[str, Any]:
    required_feature_backend = str(required_feature_backend or "").strip().lower()
    if required_feature_backend not in {"", "cpp", "python"}:
        raise ValueError(
            f"required_feature_backend must be '', 'cpp' or 'python'; got {required_feature_backend!r}"
        )
    latency_model = str(latency_model or "").strip() or "chi404_measured"
    if latency_model == "constant_order_latency":
        if entry_latency_ns is None or response_latency_ns is None:
            raise ValueError(
                "latency_model=constant_order_latency requires BOTH --entry-latency-ns and "
                "--response-latency-ns explicitly (replay band [0.5, 10] ms); omit them and "
                "use --latency-model chi404_measured to inject the owner-measured CHI404 "
                "latencies (runtime/latency_reports/latency_summary.json + latency_truth.json)"
            )
        # Refuse the whole paid campaign up front, not row-by-row.
        from backtest_pipeline.src.chi404_latency import validate_replay_latency_ms

        validate_replay_latency_ms(int(entry_latency_ns) / 1_000_000, source="cli_constant")
        validate_replay_latency_ms(int(response_latency_ns) / 1_000_000, source="cli_constant")
    elif entry_latency_ns is not None or response_latency_ns is not None:
        raise ValueError(
            "--entry-latency-ns/--response-latency-ns only apply to "
            f"--latency-model constant_order_latency; got latency_model={latency_model!r}"
        )
    first_row = _first_jsonl_row(manifest_path)
    campaign_id = str(first_row.get("campaign_id") or "hbt_campaign")
    campaign_root = Path(out_root) / safe_stem(campaign_id)
    campaign_root.mkdir(parents=True, exist_ok=True)
    settings = {
        "strategy_id": MODEL_SPECIFIC_STRATEGY_ID,
        "strategy_surface_version": MODEL_SPECIFIC_STRATEGY_SURFACE_VERSION,
        "data_contract_version": HBT_DATA_CONTRACT_VERSION,
        "requested_strategy_id": strategy_id,
        "dry_run": dry_run,
        "resume": resume,
        "maker_fee": maker_fee,
        "taker_fee": taker_fee,
        "required_feature_backend": str(required_feature_backend or ""),
        "economics_stamp": _economics_stamp(
            maker_fee,
            taker_fee,
            required_feature_backend,
            latency_model=latency_model,
            entry_latency_ns=entry_latency_ns,
            response_latency_ns=response_latency_ns,
        ),
        "latency_model": latency_model,
        "entry_latency_ns": entry_latency_ns,
        "response_latency_ns": response_latency_ns,
    }

    worker_count = max(1, int(workers))
    task_limit = _nonnegative_int(max_tasks_per_child, "max_tasks_per_child")
    status_counts: dict[str, int] = {}
    blocker_counts: dict[str, int] = {}
    row_count = 0

    def record(result: Mapping[str, Any]) -> None:
        status = str(result.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        blocker = str(result.get("blocker_code") or "")
        if blocker:
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1

    if worker_count == 1:
        for row in _iter_jsonl(manifest_path):
            row_count += 1
            record(_run_row_task((row, str(campaign_root), settings)))
    else:
        max_in_flight = worker_count * 2
        with concurrent.futures.ProcessPoolExecutor(**_process_pool_kwargs(worker_count, task_limit)) as pool:
            pending: set[concurrent.futures.Future[dict[str, Any]]] = set()
            for row in _iter_jsonl(manifest_path):
                row_count += 1
                pending.add(pool.submit(_run_row_task, (row, str(campaign_root), settings)))
                if len(pending) >= max_in_flight:
                    done, pending = concurrent.futures.wait(
                        pending,
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                    for future in done:
                        record(future.result())
            for future in concurrent.futures.as_completed(pending):
                record(future.result())
    summary = {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "campaign_manifest": str(manifest_path),
        "out_root": str(campaign_root),
        "row_count": row_count,
        "workers": worker_count,
        "max_tasks_per_child": task_limit,
        "strategy_id": MODEL_SPECIFIC_STRATEGY_ID,
        "strategy_surface_version": MODEL_SPECIFIC_STRATEGY_SURFACE_VERSION,
        "data_contract_version": HBT_DATA_CONTRACT_VERSION,
        "requested_strategy_id": str(strategy_id or MODEL_SPECIFIC_STRATEGY_ID),
        "required_feature_backend": required_feature_backend,
        "strategy_override_ignored": str(strategy_id or MODEL_SPECIFIC_STRATEGY_ID) != MODEL_SPECIFIC_STRATEGY_ID,
        "latency_model": latency_model,
        "entry_latency_ns": entry_latency_ns,
        "response_latency_ns": response_latency_ns,
        "dry_run": dry_run,
        "status_counts": dict(sorted(status_counts.items())),
        "blocker_counts": dict(sorted(blocker_counts.items())),
        "failed_count": status_counts.get("runner_failed", 0),
    }
    write_json_atomic(campaign_root / "campaign_run_summary.json", summary)
    return summary


def _process_pool_kwargs(worker_count: int, max_tasks_per_child: int) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"max_workers": worker_count}
    if max_tasks_per_child < 0:
        raise ValueError("max_tasks_per_child must be >= 0")
    if max_tasks_per_child:
        kwargs["max_tasks_per_child"] = max_tasks_per_child
        kwargs["mp_context"] = mp.get_context("spawn")
    return kwargs


def _nonnegative_int(value: Any, name: str) -> int:
    result = int(0 if value is None else value)
    if result < 0:
        raise ValueError(f"{name} must be >= 0")
    return result


def _run_row_task(task: tuple[dict[str, Any], str, Mapping[str, Any]]) -> dict[str, Any]:
    row, campaign_root_text, settings = task
    campaign_root = Path(campaign_root_text)
    row_key = str(row.get("surface_unit_id") or row.get("unit_id") or "")
    if not row_key:
        row_key = _hash_json(row)
    run_dir = campaign_root / safe_stem(row_key)
    result_path = run_dir / "campaign_row_result.json"

    strategy_id = MODEL_SPECIFIC_STRATEGY_ID

    # Blocked rows never consult the resume cache: a receipt cached under an
    # older manifest semantics (e.g. pre semantic-routing "completed" for a row
    # that is now semantic-blocked) must not be reused as evidence. The blocked
    # receipt is rewritten from the CURRENT row instead — cheap and idempotent.
    blocker_code = str(row.get("blocker_code") or "")
    metadata_blocker = _metadata_blocker(row)
    manifest_blocked = bool(blocker_code) or row.get("admissibility_status") not in ("", "admissible")
    row_is_blocked = manifest_blocked or bool(metadata_blocker)
    if settings.get("resume") and not row_is_blocked and result_path.is_file():
        cached = json.loads(result_path.read_text(encoding="utf-8"))
        if _cached_receipt_matches_run(cached, settings):
            return cached
    run_dir.mkdir(parents=True, exist_ok=True)

    if manifest_blocked:
        return _write_row_result(
            result_path,
            row,
            status="blocked_before_hbt",
            blocker_code=blocker_code or str(row.get("admissibility_status") or "pre_hbt_blocker"),
            blocker_detail=_join_blocker_details(row.get("blocker_detail"), metadata_blocker),
            hbt_run_id="",
            dry_run=bool(settings.get("dry_run")),
            economics_stamp=str(settings.get("economics_stamp") or ""),
            required_feature_backend_receipt=str(settings.get("required_feature_backend") or ""),
        )

    if metadata_blocker:
        return _write_row_result(
            result_path,
            row,
            status="blocked_before_hbt",
            blocker_code="authority_missing",
            blocker_detail=metadata_blocker,
            hbt_run_id="",
            dry_run=bool(settings.get("dry_run")),
            economics_stamp=str(settings.get("economics_stamp") or ""),
            required_feature_backend_receipt=str(settings.get("required_feature_backend") or ""),
        )

    run_id = safe_stem(row_key)
    strategy_params = dict(row.get("strategy_params") or {})
    strategy_params["model_id"] = str(row["canonical_model_id"])
    config = HftBacktestOnlyRunConfig(
        run_id=run_id,
        symbol=str(row["symbol"]),
        contract=str(row["contract"]),
        event_id=str(row["event_id"]),
        normalized_npz=Path(str(row["source_npz"])),
        initial_snapshot=Path(str(row["initial_snapshot"])),
        strategy_id=strategy_id,
        strategy_params=strategy_params,
        canonical_model_id=str(row["canonical_model_id"]),
        legacy_aliases=tuple(str(alias) for alias in row.get("legacy_aliases") or ()),
        authority_refs=tuple(str(ref) for ref in row.get("authority_refs") or ()),
        # tick/contract/fees resolve from the authoritative instrument spec
        # for row["symbol"] inside the runner; row-declared values stay in the
        # row receipt for audit but are never trusted for economics (prepared
        # units built before 2026-07 carry contract_size=1.0 stamps).
        tick_size=None,
        lot_size=float(row["lot_size"]),
        contract_size=None,
        maker_fee=None if settings.get("maker_fee") is None else float(settings["maker_fee"]),
        taker_fee=None if settings.get("taker_fee") is None else float(settings["taker_fee"]),
        required_feature_backend=str(settings.get("required_feature_backend") or ""),
        cross_asset_npz={str(k): str(v) for k, v in (row.get("cross_asset_npz") or {}).items()},
        sensor_feature_npz={str(k): str(v) for k, v in (row.get("sensor_feature_npz") or {}).items()},
        # Measured latency law: the default is the owner-measured CHI404 model;
        # constant mode reaches here only with explicit, band-checked values.
        latency_model=str(settings.get("latency_model") or "chi404_measured"),
        entry_latency_ns=(
            None if settings.get("entry_latency_ns") is None else int(settings["entry_latency_ns"])
        ),
        response_latency_ns=(
            None if settings.get("response_latency_ns") is None else int(settings["response_latency_ns"])
        ),
        event_window=dict(row.get("event_window") or {}),
    )
    try:
        result = run_hftbacktest_only(
            config,
            out_dir=run_dir,
            dry_run=bool(settings.get("dry_run")),
        )
    except Exception as exc:
        return _write_row_result(
            result_path,
            row,
            status="runner_failed",
            blocker_code="pipeline_blocker:hbt_runner_exception",
            blocker_detail=f"{type(exc).__name__}:{exc}",
            hbt_run_id=run_id,
            dry_run=bool(settings.get("dry_run")),
            economics_stamp=str(settings.get("economics_stamp") or ""),
            required_feature_backend_receipt=str(settings.get("required_feature_backend") or ""),
        )
    status = str(result.get("status") or "unknown")
    fail_closed_reasons = [str(reason) for reason in result.get("fail_closed_reasons") or ()]
    blocker_code = "" if status in {"completed", "dry_run"} else _fail_closed_blocker(status, fail_closed_reasons)
    return _write_row_result(
        result_path,
        row,
        status=status,
        blocker_code=blocker_code,
        blocker_detail=";".join(fail_closed_reasons),
        hbt_run_id=run_id,
        dry_run=bool(settings.get("dry_run")),
            economics_stamp=str(settings.get("economics_stamp") or ""),
            required_feature_backend_receipt=str(settings.get("required_feature_backend") or ""),
    )


def _metadata_blocker(row: Mapping[str, Any]) -> str:
    missing = [
        field
        for field in (
            "source_npz",
            "initial_snapshot",
            "symbol",
            "contract",
            "event_id",
            "product_metadata_source",
        )
        if not str(row.get(field) or "").strip()
    ]
    if str(row.get("metadata_policy") or "") != PRODUCT_METADATA_POLICY:
        missing.append("metadata_policy")
    authority_refs = _as_list(row.get("authority_refs"))
    product_metadata_source = str(row.get("product_metadata_source") or "").strip()
    if not authority_refs:
        missing.append("authority_refs")
    if product_metadata_source and product_metadata_source not in [str(ref) for ref in authority_refs]:
        missing.append("product_metadata_source_authority_ref")
    for field in ("tick_size", "lot_size", "contract_size"):
        try:
            if float(row.get(field)) <= 0:
                missing.append(field)
        except (TypeError, ValueError):
            missing.append(field)
    return "missing_hbt_run_metadata:" + ",".join(missing) if missing else ""


INSTRUMENT_ECONOMICS_VERSION = "instrument_specs_v1"


def _economics_stamp(
    maker_fee: float | None,
    taker_fee: float | None,
    required_feature_backend: str = "",
    latency_model: str = "chi404_measured",
    entry_latency_ns: int | None = None,
    response_latency_ns: int | None = None,
) -> str:
    """Cache key for row receipts. Receipts priced under different fee or
    tick/multiplier resolution rules, a different feature backend, or a
    different replay latency model must never satisfy --resume; receipts
    written before this stamp existed always mismatch and re-run."""
    maker = "resolved" if maker_fee is None else f"{float(maker_fee):g}"
    taker = "resolved" if taker_fee is None else f"{float(taker_fee):g}"
    backend = str(required_feature_backend or "") or "any"
    latency = str(latency_model or "chi404_measured")
    if latency == "constant_order_latency":
        latency = f"{latency}@{int(entry_latency_ns or 0)}/{int(response_latency_ns or 0)}"
    return (
        f"{INSTRUMENT_ECONOMICS_VERSION}:maker={maker}:taker={taker}"
        f":latency={latency}:backend={backend}"
    )


def _cached_receipt_matches_run(cached: Mapping[str, Any], settings: Mapping[str, Any]) -> bool:
    if str(cached.get("strategy_id") or "") != MODEL_SPECIFIC_STRATEGY_ID:
        return False
    if str(cached.get("economics_stamp") or "") != str(settings.get("economics_stamp") or ""):
        return False
    if str(cached.get("strategy_surface_version") or "") != MODEL_SPECIFIC_STRATEGY_SURFACE_VERSION:
        return False
    if str(cached.get("data_contract_version") or "") != HBT_DATA_CONTRACT_VERSION:
        return False
    current_dry_run = bool(settings.get("dry_run"))
    cached_status = str(cached.get("status") or "")
    cached_dry_run = bool(cached.get("dry_run", cached_status == "dry_run"))
    return cached_dry_run == current_dry_run


def _join_blocker_details(*details: Any) -> str:
    return ";".join(str(detail) for detail in details if str(detail or "").strip())


def _fail_closed_blocker(status: str, reasons: list[str]) -> str:
    reason = reasons[0] if reasons else status
    if (
        reason.startswith(
            ("semantic_blocker:", "pipeline_blocker:", "data_blocker:", "authority_missing:")
        )
        or reason == "authority_missing"
    ):
        return reason
    if status == "data_invalid" or reason.startswith(
        (
            "DATA_",
            "EVENT_",
            "INITIAL_",
            "TIMESTAMP_",
            "NEGATIVE_",
            "L2_L3_",
            "L3_",
            "FUTURE_DATA_",
        )
    ):
        return f"data_blocker:{reason}"
    if reason.startswith("HFTBACKTEST_VALIDATE_EVENT_ORDER_FAILED"):
        return f"data_blocker:{reason}"
    return f"pipeline_blocker:{reason}"


def _write_row_result(
    path: Path,
    row: Mapping[str, Any],
    *,
    status: str,
    blocker_code: str,
    blocker_detail: str,
    hbt_run_id: str,
    dry_run: bool,
    economics_stamp: str = "",
    required_feature_backend_receipt: str = "",
) -> dict[str, Any]:
    payload = {
        "schema_version": ROW_RESULT_SCHEMA_VERSION,
        "campaign_id": row.get("campaign_id", ""),
        "unit_id": row.get("unit_id", ""),
        "surface_unit_id": row.get("surface_unit_id", ""),
        "canonical_model_id": row.get("canonical_model_id", ""),
        "legacy_aliases": _as_list(row.get("legacy_aliases")),
        "registry_hash": row.get("registry_hash", ""),
        "source_npz": row.get("source_npz", ""),
        "source_npz_sha256": row.get("source_npz_sha256", ""),
        "symbol": row.get("symbol", ""),
        "contract": row.get("contract", ""),
        "event_id": row.get("event_id", ""),
        "event_window": row.get("event_window", {}),
        "initial_snapshot": row.get("initial_snapshot", ""),
        "initial_snapshot_sha256": row.get("initial_snapshot_sha256", ""),
        "parameter_family": row.get("parameter_family", ""),
        "parameter_hash": row.get("parameter_hash", ""),
        "strategy_id": MODEL_SPECIFIC_STRATEGY_ID,
        "strategy_surface_version": MODEL_SPECIFIC_STRATEGY_SURFACE_VERSION,
        "data_contract_version": HBT_DATA_CONTRACT_VERSION,
        "economics_stamp": economics_stamp,
        "required_feature_backend": required_feature_backend_receipt,
        "strategy_params": row.get("strategy_params", {}),
        "parameter_proposal_status": row.get("parameter_proposal_status", ""),
        "objective_evaluations": row.get("objective_evaluations", ""),
        "optimizer_claim": row.get("optimizer_claim", ""),
        "tick_size": row.get("tick_size"),
        "lot_size": row.get("lot_size"),
        "contract_size": row.get("contract_size"),
        "product_metadata_source": row.get("product_metadata_source", ""),
        "metadata_policy": row.get("metadata_policy", ""),
        "status": status,
        "dry_run": dry_run,
        "blocker_code": blocker_code,
        "blocker_detail": blocker_detail,
        "adapter_status": row.get("adapter_status", ""),
        "authority_refs": _as_list(row.get("authority_refs")),
        "hbt_run_id": hbt_run_id,
        "hbt_run_status": status,
        "promotion_decision_path": str(path.parent / "promotion_decision.json")
        if (path.parent / "promotion_decision.json").is_file()
        else "",
    }
    write_json_atomic(path, payload)
    return payload


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _first_jsonl_row(path: Path) -> dict[str, Any]:
    rows = _iter_jsonl(path)
    try:
        return next(rows)
    except StopIteration as exc:
        raise SystemExit("--campaign-manifest has no rows") from exc
    finally:
        rows.close()


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, Mapping):
                raise SystemExit("--campaign-manifest rows must be JSON objects")
            yield dict(payload)


def _hash_json(payload: Mapping[str, Any]) -> str:
    import hashlib

    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


AUTO_WORKERS_CPU_FRACTION = 0.85


def resolve_workers(value: str | int) -> int:
    """`auto` = 85% of logical cores (floor), leaving headroom for the OS and
    the coordinator; the 2026-07-02 canary ran 96 explicit workers on a
    192-core box — half idle."""
    if isinstance(value, str) and value.strip().lower() == "auto":
        import os

        cores = os.cpu_count() or 1
        return max(1, int(cores * AUTO_WORKERS_CPU_FRACTION))
    workers = int(value)
    if workers < 1:
        raise ValueError("--workers must be >= 1 or 'auto'")
    return workers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a full HBT-only campaign manifest")
    parser.add_argument("--campaign-manifest", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, default=REPO / "artifacts" / "hbt_campaign_runs")
    parser.add_argument(
        "--workers",
        default="1",
        help="Worker process count, or 'auto' for floor(0.85 x logical cores).",
    )
    parser.add_argument(
        "--max-tasks-per-child",
        type=int,
        default=0,
        help="Recycle process-pool workers after N row tasks; 0 disables recycling.",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--maker-fee",
        type=float,
        default=None,
        help="Explicit per-side maker fee override; unset resolves the product's real fee",
    )
    parser.add_argument(
        "--taker-fee",
        type=float,
        default=None,
        help="Explicit per-side taker fee override; unset resolves the product's real fee",
    )
    parser.add_argument(
        "--required-feature-backend",
        default="cpp",
        help="Feature backend evidence receipts must carry (cpp/python); "
        "empty string accepts any (local smokes only).",
    )
    parser.add_argument(
        "--latency-model",
        default="chi404_measured",
        help=(
            "chi404_measured[:regime] (default) injects the owner-measured CHI404 "
            "native-probe latencies (entry leg includes the measured offensive "
            "tick->send p99); constant_order_latency requires BOTH "
            "--entry-latency-ns and --response-latency-ns explicitly, "
            "band-checked against [0.5, 10] ms."
        ),
    )
    parser.add_argument(
        "--entry-latency-ns",
        type=int,
        default=None,
        help="Explicit constant entry latency; required (with --response-latency-ns) "
        "for --latency-model constant_order_latency, forbidden otherwise.",
    )
    parser.add_argument(
        "--response-latency-ns",
        type=int,
        default=None,
        help="Explicit constant response latency; required (with --entry-latency-ns) "
        "for --latency-model constant_order_latency, forbidden otherwise.",
    )
    args = parser.parse_args(argv)
    if args.max_tasks_per_child < 0:
        parser.error("--max-tasks-per-child must be >= 0")
    try:
        workers = resolve_workers(args.workers)
    except ValueError as exc:
        parser.error(str(exc))

    summary = run_campaign(
        manifest_path=args.campaign_manifest,
        out_root=args.out_root,
        dry_run=args.dry_run,
        workers=workers,
        max_tasks_per_child=args.max_tasks_per_child,
        resume=args.resume,
        maker_fee=args.maker_fee,
        taker_fee=args.taker_fee,
        required_feature_backend=args.required_feature_backend,
        latency_model=args.latency_model,
        entry_latency_ns=args.entry_latency_ns,
        response_latency_ns=args.response_latency_ns,
    )
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0 if summary["failed_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
