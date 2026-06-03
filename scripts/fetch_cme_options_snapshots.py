"""Fetch real CME options snapshot windows from a generated plan.

Live downloads require both --confirm-purchase and DATABENTO_API_KEY. Dry-run
mode works without an API key and records which rows could not be estimated.
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

WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE))
sys.path.insert(0, str(WORKTREE / "packages"))

EXECUTABLE_POLICY = "configured_cme_options_symbol"


def _parse_utc(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_plan(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _filter_executable(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row for row in plan
        if row.get("download_now") is True
        and row.get("download_policy") == EXECUTABLE_POLICY
        and str(row.get("options_symbol", "")).strip()
    ]


def _redact(value: str) -> str:
    api_key = os.getenv("DATABENTO_API_KEY", "")
    if api_key and api_key in value:
        return value.replace(api_key, "***REDACTED***")
    return value


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return slug.strip("_") or "snapshot"


def _paths_for_row(output_dir: Path, row: dict[str, Any]) -> tuple[Path, Path]:
    future = _safe_slug(str(row["future_symbol"]))
    event_id = _safe_slug(str(row["event_id"]))
    option = _safe_slug(str(row["options_symbol_label"]))
    offset = int(row["offset_sec"])
    end = _parse_utc(str(row["options_window_end_utc"]))
    schema = _safe_slug(str(row.get("schema", "mbp-1")))
    base = f"{end.strftime('%Y-%m-%d_%H%M%S')}_offset_{offset:+d}_{option}_{schema}"
    raw = output_dir / "raw" / future / event_id / f"{base}.dbn.zst"
    failure = output_dir / "failures" / future / event_id / f"{base}.json"
    return raw, failure


def _estimate_row_cost(row: dict[str, Any]) -> float:
    from data_system.src.databento_client import DatabentoResearchClient  # type: ignore

    client = DatabentoResearchClient()
    return client.estimate_cost(
        symbols=[row["options_symbol"]],
        start_utc=_parse_utc(row["options_window_start_utc"]),
        end_utc=_parse_utc(row["options_window_end_utc"]),
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

    raw_path, _ = _paths_for_row(output_dir, row)
    if raw_path.exists() and raw_path.stat().st_size > 0:
        return {
            "status": "skipped_already_downloaded",
            "raw_path": str(raw_path),
            "size_bytes": raw_path.stat().st_size,
        }
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    client = DatabentoResearchClient()
    event_id = _safe_slug(
        f"cme_options_{row['event_id']}_{row['future_symbol']}_{row['options_symbol_label']}_offset_{row['offset_sec']}"
    )
    client.download_event_window(
        event_id=event_id,
        symbols=[row["options_symbol"]],
        start_utc=_parse_utc(row["options_window_start_utc"]),
        end_utc=_parse_utc(row["options_window_end_utc"]),
        dataset=row["dataset"],
        schema=row["schema"],
        stype_in=row["stype_in"],
        requested_symbol=str(row["future_symbol"]),
        output_path=str(raw_path),
        override_hard_limit=override_hard_limit,
        override_operating_cap=override_operating_cap,
    )
    return {"status": "downloaded", "raw_path": str(raw_path)}


def _is_terminal_symbology_failure(message: str) -> bool:
    msg = message.lower()
    return "symbology" in msg or "none of the symbols could be resolved" in msg


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
        raw_path, failure_path = _paths_for_row(output_dir, row)
        rec = {
            "event_id": row["event_id"],
            "future_symbol": row["future_symbol"],
            "options_symbol": row["options_symbol"],
            "options_symbol_label": row["options_symbol_label"],
            "offset_sec": row["offset_sec"],
            "dataset": row["dataset"],
            "schema": row["schema"],
            "stype_in": row["stype_in"],
            "options_window_start_utc": row["options_window_start_utc"],
            "options_window_end_utc": row["options_window_end_utc"],
            "raw_path": str(raw_path),
            "failure_path": str(failure_path),
        }
        if raw_path.exists() and raw_path.stat().st_size > 0:
            rec["status"] = "skipped_already_downloaded"
            rec["size_bytes"] = raw_path.stat().st_size
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
                    json.dumps(
                        {
                            "status": "terminal_symbology_failure",
                            "event_id": row["event_id"],
                            "future_symbol": row["future_symbol"],
                            "options_symbol": row["options_symbol"],
                            "error": rec["estimate_error"],
                        },
                        indent=2,
                        sort_keys=True,
                    ),
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
                rec.update(
                    _download_row(
                        row,
                        output_dir,
                        override_operating_cap=override_operating_cap,
                        override_hard_limit=override_hard_limit,
                    )
                )
                if rec.get("status") == "downloaded":
                    n_downloaded += 1
            except Exception as exc:  # noqa: BLE001
                rec["status"] = "download_failed"
                rec["error"] = _redact(f"{type(exc).__name__}: {exc}")
        records.append(rec)

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest["records"] = records
    manifest["estimated_total_cost_usd"] = round(running_total, 6)
    manifest["n_priced"] = n_priced
    manifest["n_downloaded"] = n_downloaded
    manifest["status"] = "completed" if dry_run else "live_completed"
    (output_dir / "cme_options_snapshot_fetch_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scripts.fetch_cme_options_snapshots")
    p.add_argument("--plan", required=True, help="Path to cme_options_snapshot_plan.json")
    p.add_argument("--output-dir", default="research_cards/cme/options_snapshots")
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
