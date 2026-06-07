"""Fetch L2/L3 (MBO) windows from Databento, gated by the free-daily benchmark plan.

Reads `l2_l3_minimal_pull_plan.json` produced by
`equities_lane.pipeline resolve-runner-seeds` and only acts on rows where
`download_now=true` and `download_policy=free_daily_benchmark_passed`.

Behavior:
  - dry-run (default): groups windows by ticker, estimates cost per ticker
    using Databento metadata.get_cost, sums, writes a manifest. No API
    download is performed and no API key is required.
  - confirmed download (--confirm-purchase): requires DATABENTO_API_KEY in env, calls
    the existing DatabentoResearchClient per ticker, and respects the
    BudgetManager hard limit and operating cap. Use --override-operating-cap
    / --override-hard-limit to bypass the gated layers if you have
    approved spend.

No paid market data is downloaded without an explicit confirm flag.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _parse_et(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt.astimezone(timezone.utc)


def _load_plan(plan_path: Path) -> list[dict[str, Any]]:
    return json.loads(plan_path.read_text(encoding="utf-8"))


def _filter_executable(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in plan:
        if row.get("download_now") is True and row.get("download_policy") == "free_daily_benchmark_passed":
            out.append(row)
    return out


def _group_by_ticker(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["ticker"]].append(row)
    return grouped


def _ticker_window_bounds(rows: list[dict[str, Any]]) -> tuple[datetime, datetime, int]:
    starts = [_parse_et(r["window_start_et"]) for r in rows]
    ends = [_parse_et(r["window_end_et"]) for r in rows]
    return min(starts), max(ends), len(rows)


def _default_dataset() -> str:
    return os.getenv("DATABENTO_DATASET", "XNAS.ITCH")


def _default_stype_in() -> str:
    return os.getenv("DATABENTO_STYPE_IN", "raw_symbol")


def _estimate_ticker_cost(
    ticker: str,
    start_utc: datetime,
    end_utc: datetime,
    dataset: str,
    schema: str,
    stype_in: str,
) -> float:
    from data_system.src.databento_client import DatabentoResearchClient  # type: ignore

    client = DatabentoResearchClient()
    return client.estimate_cost(
        symbols=[ticker],
        start_utc=start_utc,
        end_utc=end_utc,
        dataset=dataset,
        schema=schema,
        stype_in=stype_in,
    )


def _estimate_available() -> bool:
    return bool(os.getenv("DATABENTO_API_KEY"))


def _redact(value: str) -> str:
    api_key = os.getenv("DATABENTO_API_KEY", "")
    if api_key and api_key in value:
        return value.replace(api_key, "***REDACTED***")
    return value


def _download_ticker_windows(
    ticker: str,
    rows: list[dict[str, Any]],
    out_dir: Path,
    dataset: str,
    schema: str,
    stype_in: str,
    override_operating_cap: bool,
    override_hard_limit: bool,
) -> dict[str, Any]:
    from data_system.src.databento_client import DatabentoResearchClient  # type: ignore

    start_utc, end_utc, _ = _ticker_window_bounds(rows)
    ticker_dir = out_dir / ticker.upper()
    ticker_dir.mkdir(parents=True, exist_ok=True)
    start_iso = start_utc.date().isoformat()
    end_iso = end_utc.date().isoformat()
    out_path = ticker_dir / f"{start_iso}_{end_iso}_{schema}.dbn.zst"
    if out_path.exists() and out_path.stat().st_size > 0:
        return {
            "ticker": ticker,
            "n_windows": len(rows),
            "start_utc": start_utc.isoformat(),
            "end_utc": end_utc.isoformat(),
            "output_path": str(out_path),
            "status": "skipped_already_downloaded",
            "size_bytes": out_path.stat().st_size,
        }
    client = DatabentoResearchClient()
    event_id = f"l2l3_{ticker.upper()}_{start_iso}_{end_iso}".replace(".", "_")
    client.download_event_window(
        event_id=event_id,
        symbols=[ticker.upper()],
        start_utc=start_utc,
        end_utc=end_utc,
        dataset=dataset,
        schema=schema,
        stype_in=stype_in,
        requested_symbol=ticker,
        output_path=str(out_path),
        override_hard_limit=override_hard_limit,
        override_operating_cap=override_operating_cap,
    )
    return {
        "ticker": ticker,
        "n_windows": len(rows),
        "start_utc": start_utc.isoformat(),
        "end_utc": end_utc.isoformat(),
        "output_path": str(out_path),
        "status": "downloaded",
    }


def build_manifest(
    plan_path: Path,
    output_dir: Path,
    *,
    dry_run: bool,
    confirm_purchase: bool,
    dataset: str,
    schema: str,
    stype_in: str,
    override_operating_cap: bool,
    override_hard_limit: bool,
    max_total_cost_usd: float | None,
    max_tickers: int | None,
) -> dict[str, Any]:
    plan = _load_plan(plan_path)
    executable = _filter_executable(plan)
    skipped_plan_only = [r for r in plan if r.get("download_policy") == "plan_only_until_free_daily_benchmark_passes"]
    grouped = _group_by_ticker(executable)

    has_api_key = bool(os.getenv("DATABENTO_API_KEY"))
    manifest: dict[str, Any] = {
        "mode": "dry_run" if dry_run else "confirmed_download",
        "plan_path": str(plan_path),
        "output_dir": str(output_dir),
        "dataset": dataset,
        "schema": schema,
        "stype_in": stype_in,
        "n_plan_rows": len(plan),
        "n_plan_only_rows": len(skipped_plan_only),
        "n_executable_rows": len(executable),
        "n_executable_tickers": len(grouped),
        "databento_api_key_present": has_api_key,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    if not dry_run and not confirm_purchase:
        manifest["status"] = "refused"
        manifest["refusal_reason"] = "--confirm-purchase required for confirmed downloads"
        return manifest
    if not dry_run and not has_api_key:
        manifest["status"] = "refused"
        manifest["refusal_reason"] = "DATABENTO_API_KEY env var is required for confirmed downloads"
        return manifest

    ticker_records: list[dict[str, Any]] = []
    running_total = 0.0
    n_cost_overrides = 0
    n_priced = 0
    estimate_available = _estimate_available()
    for ticker, rows in sorted(grouped.items()):
        start_utc, end_utc, n_windows = _ticker_window_bounds(rows)
        rec: dict[str, Any] = {
            "ticker": ticker,
            "n_windows": n_windows,
            "start_utc": start_utc.isoformat(),
            "end_utc": end_utc.isoformat(),
            "snapshot_names": sorted({r["snapshot_name"] for r in rows}),
        }
        if not estimate_available and dry_run:
            rec["status"] = "dry_run_no_estimate_no_api_key"
            rec["note"] = "set DATABENTO_API_KEY to estimate cost without downloading"
            ticker_records.append(rec)
            continue

        if max_tickers is not None and n_priced >= max_tickers:
            rec["status"] = "skipped_max_tickers"
            ticker_records.append(rec)
            continue

        try:
            cost = _estimate_ticker_cost(ticker, start_utc, end_utc, dataset, schema, stype_in)
        except Exception as exc:  # noqa: BLE001
            rec["status"] = "estimate_failed"
            rec["estimate_error"] = _redact(f"{type(exc).__name__}: {exc}")
            ticker_records.append(rec)
            continue

        if (
            max_total_cost_usd is not None
            and (running_total + cost) > max_total_cost_usd
            and not override_operating_cap
        ):
            rec["status"] = "skipped_total_cost_cap"
            rec["would_be_cost_usd"] = round(cost, 4)
            ticker_records.append(rec)
            continue

        rec["estimated_cost_usd"] = round(cost, 4)
        running_total += cost
        n_priced += 1

        if dry_run:
            rec["status"] = "dry_run_estimate"
        else:
            try:
                result = _download_ticker_windows(
                    ticker,
                    rows,
                    output_dir,
                    dataset,
                    schema,
                    stype_in,
                    override_operating_cap=override_operating_cap,
                    override_hard_limit=override_hard_limit,
                )
                rec.update(result)
            except Exception as exc:  # noqa: BLE001
                rec["status"] = "download_failed"
                rec["error"] = _redact(f"{type(exc).__name__}: {exc}")
                n_cost_overrides += 1
        ticker_records.append(rec)

    manifest["ticker_records"] = ticker_records
    manifest["estimated_total_cost_usd"] = round(running_total, 4)
    manifest["n_cost_overrides"] = n_cost_overrides
    manifest["status"] = "completed" if dry_run else "confirmed_download_completed"

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "l2_l3_fetch_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.fetch_l2_l3_databento")
    parser.add_argument("--plan", required=True, help="Path to l2_l3_minimal_pull_plan.json")
    parser.add_argument("--output-dir", required=True, help="Directory for downloaded MBO files and manifest")
    parser.add_argument("--dataset", default=_default_dataset())
    parser.add_argument("--schema", default="mbo")
    parser.add_argument("--stype-in", default=_default_stype_in())
    parser.add_argument("--dry-run", action="store_true", help="Estimate cost only; no download, no key required")
    parser.add_argument("--confirm-purchase", action="store_true", help="Required for confirmed downloads")
    parser.add_argument("--override-operating-cap", action="store_true")
    parser.add_argument("--override-hard-limit", action="store_true")
    parser.add_argument("--max-total-cost-usd", type=float, default=None)
    parser.add_argument("--max-tickers", type=int, default=None)
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip tickers whose .dbn.zst output file already exists on disk.",
    )
    args = parser.parse_args(argv)

    plan_path = Path(args.plan)
    if not plan_path.exists():
        print(f"plan not found: {plan_path}", file=sys.stderr)
        return 2

    if not args.dry_run and not args.confirm_purchase:
        print("refusing to run: pass --dry-run or --confirm-purchase", file=sys.stderr)
        return 3

    if not args.dry_run and not os.getenv("DATABENTO_API_KEY"):
        print("refusing to run: DATABENTO_API_KEY must be set for confirmed downloads", file=sys.stderr)
        return 4

    manifest = build_manifest(
        plan_path,
        Path(args.output_dir),
        dry_run=args.dry_run,
        confirm_purchase=args.confirm_purchase,
        dataset=args.dataset,
        schema=args.schema,
        stype_in=args.stype_in,
        override_operating_cap=args.override_operating_cap,
        override_hard_limit=args.override_hard_limit,
        max_total_cost_usd=args.max_total_cost_usd,
        max_tickers=args.max_tickers,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest.get("status") in {"completed", "confirmed_download_completed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
