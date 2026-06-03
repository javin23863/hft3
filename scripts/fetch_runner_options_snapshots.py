"""Fetch OPRA options feature snapshots aligned to equity runner snapshots.

This is an equities-lane options feature, not a separate options lane. It
reads `options_snapshot_plan.json` produced by
`equities_lane.pipeline resolve-runner-seeds` and pulls OPRA parent-chain
`cbbo-1m` windows for the same point-in-time snapshots as the equity plan.

Dry-run mode estimates cost only. Live mode requires `--confirm-purchase` and
`DATABENTO_API_KEY`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE))
sys.path.insert(0, str(WORKTREE / "packages"))

from equities_lane.src.ingest.options_chain_pull import normalize_options_dbn  # noqa: E402

ET = ZoneInfo("America/New_York")


def _parse_et(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt.astimezone(timezone.utc)


def _load_plan(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _filter_executable(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row for row in plan
        if row.get("download_now") is True and row.get("download_policy") == "free_daily_benchmark_passed"
    ]


def _redact(value: str) -> str:
    api_key = os.getenv("DATABENTO_API_KEY", "")
    if api_key and api_key in value:
        return value.replace(api_key, "***REDACTED***")
    return value


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return slug.strip("_") or "snapshot"


def _artifact_base(row: dict[str, Any]) -> tuple[str, str, str]:
    ticker = str(row["ticker"]).upper()
    event_id = _safe_slug(str(row["runner_label_id"]))
    snap = _safe_slug(str(row["snapshot_name"]).lower())
    start_dt = datetime.fromisoformat(str(row["options_window_start_et"]))
    end_dt = datetime.fromisoformat(str(row["options_window_end_et"]))
    start_date = start_dt.date().isoformat()
    time_span = f"{start_dt.strftime('%H%M%S')}_{end_dt.strftime('%H%M%S')}"
    schema = str(row.get("schema", "cbbo-1m"))
    base = f"{start_date}_{time_span}_{snap}_{schema}"
    return ticker, event_id, base


def _paths_for_row(output_dir: Path, row: dict[str, Any]) -> tuple[Path, Path]:
    ticker, event_id, base = _artifact_base(row)
    raw = output_dir / "raw" / ticker / event_id / f"{base}.dbn.zst"
    norm = output_dir / "normalized" / ticker / event_id / f"{base}.ndjson"
    return raw, norm


def _failure_path_for_row(output_dir: Path, row: dict[str, Any]) -> Path:
    ticker, event_id, base = _artifact_base(row)
    return output_dir / "failures" / ticker / event_id / f"{base}.json"


def _estimate_row_cost(row: dict[str, Any]) -> float:
    from data_system.src.databento_client import DatabentoResearchClient  # type: ignore

    client = DatabentoResearchClient()
    return client.estimate_cost(
        symbols=[row["underlying_parent_symbol"]],
        start_utc=_parse_et(row["options_window_start_et"]),
        end_utc=_parse_et(row["options_window_end_et"]),
        dataset=row["dataset"],
        schema=row["schema"],
        stype_in=row["stype_in"],
    )


def _download_row(
    row: dict[str, Any],
    output_dir: Path,
    *,
    override_operating_cap: bool,
    override_hard_limit: bool,
) -> dict[str, Any]:
    from data_system.src.databento_client import DatabentoResearchClient  # type: ignore

    raw_path, norm_path = _paths_for_row(output_dir, row)
    if norm_path.exists() and norm_path.stat().st_size > 0:
        return {
            "status": "skipped_already_normalized",
            "raw_path": str(raw_path),
            "normalized_path": str(norm_path),
            "normalized_size_bytes": norm_path.stat().st_size,
        }
    if raw_path.exists() and raw_path.stat().st_size > 0:
        count = normalize_options_dbn(
            raw_path,
            norm_path,
            session_id=str(row["runner_label_id"]),
            underlying=str(row["ticker"]).upper(),
        )
        return {
            "status": "normalized_existing_raw",
            "raw_path": str(raw_path),
            "normalized_path": str(norm_path),
            "resolved_symbol_count": count,
        }

    raw_path.parent.mkdir(parents=True, exist_ok=True)
    norm_path.parent.mkdir(parents=True, exist_ok=True)
    client = DatabentoResearchClient()
    event_id = f"runner_options_{row['runner_label_id']}_{row['snapshot_name']}".replace(".", "_")
    client.download_event_window(
        event_id=_safe_slug(event_id),
        symbols=[row["underlying_parent_symbol"]],
        start_utc=_parse_et(row["options_window_start_et"]),
        end_utc=_parse_et(row["options_window_end_et"]),
        dataset=row["dataset"],
        schema=row["schema"],
        stype_in=row["stype_in"],
        requested_symbol=str(row["ticker"]).upper(),
        output_path=str(raw_path),
        override_hard_limit=override_hard_limit,
        override_operating_cap=override_operating_cap,
    )
    count = normalize_options_dbn(
        raw_path,
        norm_path,
        session_id=str(row["runner_label_id"]),
        underlying=str(row["ticker"]).upper(),
    )
    return {
        "status": "downloaded",
        "raw_path": str(raw_path),
        "normalized_path": str(norm_path),
        "resolved_symbol_count": count,
    }


def build_manifest(
    plan_path: Path,
    output_dir: Path,
    *,
    dry_run: bool,
    confirm_purchase: bool,
    max_total_cost_usd: float | None,
    max_requests: int | None,
    override_operating_cap: bool,
    override_hard_limit: bool,
) -> dict[str, Any]:
    plan = _load_plan(plan_path)
    executable = _filter_executable(plan)
    has_api_key = bool(os.getenv("DATABENTO_API_KEY"))
    manifest: dict[str, Any] = {
        "mode": "dry_run" if dry_run else "live",
        "plan_path": str(plan_path),
        "output_dir": str(output_dir),
        "n_plan_rows": len(plan),
        "n_executable_rows": len(executable),
        "n_executable_tickers": len({r["ticker"] for r in executable}),
        "databento_api_key_present": has_api_key,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    if not dry_run and not confirm_purchase:
        manifest["status"] = "refused"
        manifest["refusal_reason"] = "--confirm-purchase required for live downloads"
        return manifest
    if not dry_run and not has_api_key:
        manifest["status"] = "refused"
        manifest["refusal_reason"] = "DATABENTO_API_KEY env var is required for live downloads"
        return manifest

    records: list[dict[str, Any]] = []
    running_total = 0.0
    n_priced = 0
    n_downloaded = 0
    for row in executable:
        raw_path, norm_path = _paths_for_row(output_dir, row)
        failure_path = _failure_path_for_row(output_dir, row)
        rec = {
            "ticker": row["ticker"],
            "runner_label_id": row["runner_label_id"],
            "snapshot_name": row["snapshot_name"],
            "underlying_parent_symbol": row["underlying_parent_symbol"],
            "dataset": row["dataset"],
            "schema": row["schema"],
            "stype_in": row["stype_in"],
            "options_window_start_et": row["options_window_start_et"],
            "options_window_end_et": row["options_window_end_et"],
            "options_market_status": row["options_market_status"],
            "raw_path": str(raw_path),
            "normalized_path": str(norm_path),
            "failure_path": str(failure_path),
        }
        if norm_path.exists():
            size = norm_path.stat().st_size
            rec["status"] = "skipped_already_normalized" if size > 0 else "skipped_already_empty_normalized"
            rec["normalized_size_bytes"] = size
            records.append(rec)
            continue
        if raw_path.exists() and raw_path.stat().st_size > 0:
            try:
                count = normalize_options_dbn(
                    raw_path,
                    norm_path,
                    session_id=str(row["runner_label_id"]),
                    underlying=str(row["ticker"]).upper(),
                )
                rec["status"] = "normalized_existing_raw"
                rec["resolved_symbol_count"] = count
            except Exception as exc:  # noqa: BLE001
                rec["status"] = "normalize_existing_raw_failed"
                rec["error"] = _redact(f"{type(exc).__name__}: {exc}")
            records.append(rec)
            continue
        if failure_path.exists() and failure_path.stat().st_size > 0:
            rec["status"] = "skipped_terminal_failure"
            try:
                rec["terminal_failure"] = json.loads(failure_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                rec["terminal_failure"] = {"path": str(failure_path)}
            records.append(rec)
            continue
        if max_requests is not None and n_priced >= max_requests:
            rec["status"] = "skipped_max_requests"
            records.append(rec)
            continue
        if not has_api_key:
            rec["status"] = "dry_run_no_estimate_no_api_key"
            records.append(rec)
            continue
        try:
            cost = _estimate_row_cost(row)
        except Exception as exc:  # noqa: BLE001
            rec["status"] = "estimate_failed"
            rec["estimate_error"] = _redact(f"{type(exc).__name__}: {exc}")
            if _is_terminal_symbology_failure(str(exc)):
                failure_path.parent.mkdir(parents=True, exist_ok=True)
                failure_path.write_text(
                    json.dumps({
                        "status": "terminal_symbology_failure",
                        "ticker": row["ticker"],
                        "underlying_parent_symbol": row["underlying_parent_symbol"],
                        "error": rec["estimate_error"],
                    }, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
            records.append(rec)
            continue
        if max_total_cost_usd is not None and (running_total + cost) > max_total_cost_usd and not override_operating_cap:
            rec["status"] = "skipped_total_cost_cap"
            rec["would_be_cost_usd"] = round(cost, 6)
            records.append(rec)
            continue
        rec["estimated_cost_usd"] = round(cost, 6)
        running_total += cost
        n_priced += 1
        if dry_run:
            rec["status"] = "dry_run_estimate"
        else:
            try:
                rec.update(_download_row(
                    row,
                    output_dir,
                    override_operating_cap=override_operating_cap,
                    override_hard_limit=override_hard_limit,
                ))
                if rec.get("status") in {"downloaded", "normalized_existing_raw"}:
                    n_downloaded += 1
            except Exception as exc:  # noqa: BLE001
                rec["status"] = "download_failed"
                rec["error"] = _redact(f"{type(exc).__name__}: {exc}")
        records.append(rec)

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest["ticker_records"] = records
    manifest["estimated_total_cost_usd"] = round(running_total, 6)
    manifest["n_priced"] = n_priced
    manifest["n_downloaded"] = n_downloaded
    manifest["status"] = "completed" if dry_run else "live_completed"
    (output_dir / "options_snapshot_fetch_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def _is_terminal_symbology_failure(message: str) -> bool:
    return "symbology_invalid_request" in message or "None of the symbols could be resolved" in message


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scripts.fetch_runner_options_snapshots")
    p.add_argument("--plan", required=True, help="Path to options_snapshot_plan.json")
    p.add_argument("--output-dir", default="research_cards/equities/options_snapshots")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--confirm-purchase", action="store_true")
    p.add_argument("--max-total-cost-usd", type=float, default=None)
    p.add_argument("--max-requests", type=int, default=None)
    p.add_argument("--override-operating-cap", action="store_true")
    p.add_argument("--override-hard-limit", action="store_true")
    args = p.parse_args(argv)

    plan_path = Path(args.plan)
    if not plan_path.exists():
        print(f"plan not found: {plan_path}", file=sys.stderr)
        return 2
    if not args.dry_run and not args.confirm_purchase:
        print("refusing to run: pass --dry-run or --confirm-purchase", file=sys.stderr)
        return 3
    if not args.dry_run and not os.getenv("DATABENTO_API_KEY"):
        print("refusing to run: DATABENTO_API_KEY must be set for live downloads", file=sys.stderr)
        return 4

    manifest = build_manifest(
        plan_path,
        Path(args.output_dir),
        dry_run=args.dry_run,
        confirm_purchase=args.confirm_purchase,
        max_total_cost_usd=args.max_total_cost_usd,
        max_requests=args.max_requests,
        override_operating_cap=args.override_operating_cap,
        override_hard_limit=args.override_hard_limit,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest.get("status") in {"completed", "live_completed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
