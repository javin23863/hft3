"""Fetch daily OHLCV for known delisted small-cap seed tickers.

This is a paid-source remediation path for tickers that have no free daily
history (see `delisted_seed_tickers` in the seed config). It is NOT part of
the free-data phase. By default it runs in dry-run mode and produces a plan
manifest; it refuses to run a confirmed download without both:

- `--confirm-purchase` on the command line
- `DATABENTO_API_KEY` in the environment

The script reads the `delisted_seed_tickers.paid_remediation` block from the
seed config, builds a per-ticker (start, end) date window from the cohort
year, and estimates/downloads the ohlcv-1d bars. Per-ticker CSVs are
written under the configured `output_root` (default
`data/equities/daily_delimited/`) in the DailyBar format:
`symbol,date,open,high,low,close,volume` (plus `adjclose` for tooling
compatibility).

The .dbn.zst file Databento returns is removed after conversion to keep the
workspace tidy; the CSV is the only persistent artifact.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

WORKTREE = Path(__file__).resolve().parent
REPO = WORKTREE.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "packages"))

from equities_lane.src.prediction.runner_seed_resolver import load_seed_config  # noqa: E402

try:
    import databento as db
except ImportError:  # noqa: BLE001
    db = None  # type: ignore[assignment]


@dataclass(frozen=True)
class PullPlan:
    ticker: str
    cohort: str
    target_year: int | None
    start_iso: str
    end_iso: str
    dataset: str
    schema: str
    stype_in: str
    output_path: Path


def _redact(text: str) -> str:
    key = os.environ.get("DATABENTO_API_KEY", "")
    if not key:
        return text
    quoted = urllib.parse.quote(key, safe="")
    return text.replace(key, "[REDACTED]").replace(quoted, "[REDACTED]")


def _load_paid_block(cfg: dict[str, Any]) -> dict[str, Any]:
    block = (cfg.get("delisted_seed_tickers") or {}).get("paid_remediation")
    if not isinstance(block, dict):
        raise ValueError(
            "delisted_seed_tickers.paid_remediation block missing from config; cannot build a pull plan."
        )
    required = {"source", "dataset", "schema", "stype_in", "output_root"}
    missing = sorted(k for k in required if k not in block)
    if missing:
        raise ValueError(f"paid_remediation missing keys: {missing}")
    return block


def _cohort_year(cohort: str) -> int | None:
    text = str(cohort)
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    return None


def _window_dates(cohort: str, block: dict[str, Any]) -> tuple[str, str]:
    yr = _cohort_year(cohort)
    if yr is None:
        return ("2022-01-01", "2026-01-01")
    spec = (block.get("date_range_per_ticker") or {})
    start_offset = int(spec.get("start_offset_days", -30))
    end_offset = int(spec.get("end_offset_days", 365))
    start = date(yr, 1, 1) + timedelta(days=start_offset)
    end = date(yr, 12, 31) + timedelta(days=end_offset)
    return (start.isoformat(), end.isoformat())


def _load_delisted_tickers(cfg: dict[str, Any]) -> dict[str, str]:
    raw = ((cfg.get("delisted_seed_tickers") or {}).get("known_delisted") or [])
    if not raw:
        return {}
    cohorts = cfg.get("positive_seed_tickers") or {}
    by_ticker: dict[str, str] = {}
    for cohort, tickers in cohorts.items():
        for t in tickers or []:
            sym = str(t).strip().upper()
            if sym:
                by_ticker[sym] = str(cohort)
    out: dict[str, str] = {}
    for t in raw:
        sym = str(t).strip().upper()
        if not sym:
            continue
        cohort = by_ticker.get(sym, "unknown")
        out[sym] = cohort
    return out


def build_pull_plan(cfg: dict[str, Any]) -> list[PullPlan]:
    block = _load_paid_block(cfg)
    tickers = _load_delisted_tickers(cfg)
    if not tickers:
        return []
    output_root = Path(str(block.get("output_root", "data/equities/daily_delisted")))
    if not output_root.is_absolute():
        output_root = (REPO / output_root).resolve()
    plans: list[PullPlan] = []
    for ticker, cohort in sorted(tickers.items()):
        start, end = _window_dates(cohort, block)
        plans.append(PullPlan(
            ticker=ticker,
            cohort=cohort,
            target_year=_cohort_year(cohort),
            start_iso=start,
            end_iso=end,
            dataset=str(block["dataset"]),
            schema=str(block["schema"]),
            stype_in=str(block["stype_in"]),
            output_path=output_root / f"{ticker}.csv",
        ))
    return plans


def _dbn_to_csv_rows(dbn_path: Path, ticker: str) -> list[dict[str, Any]]:
    """Read a .dbn.zst file and return rows in DailyBar-compatible dict form."""
    if db is None:
        raise RuntimeError("databento library not installed; cannot parse .dbn.zst")
    store = db.DBNStore.from_file(str(dbn_path))
    df = store.to_df()
    if df is None or df.empty:
        return []
    ts = df.index.get_level_values("ts_event") if "ts_event" in df.index.names else df.index
    ts = pd_to_datetime(ts)
    rows: list[dict[str, Any]] = []
    for ts_value, o, h, l, c, v in zip(
        ts,
        df["open"].tolist(),
        df["high"].tolist(),
        df["low"].tolist(),
        df["close"].tolist(),
        df["volume"].tolist(),
    ):
        if any(x is None for x in (o, h, l, c, v)):
            continue
        date_str = pd_ts_to_date(ts_value)
        rows.append({
            "symbol": ticker,
            "date": date_str,
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": float(v),
            "adjclose": float(c),
        })
    return rows


def pd_to_datetime(values):  # minimal shim so we don't add pandas as a hard dep
    try:
        import pandas as pd
        return pd.to_datetime(values)
    except Exception:
        return list(values)


def pd_ts_to_date(value) -> str:
    try:
        import pandas as pd
        if isinstance(value, pd.Timestamp):
            return value.date().isoformat()
    except Exception:
        pass
    if isinstance(value, datetime):
        return value.date().isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return str(value)[:10]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ["symbol", "date", "open", "high", "low", "close", "volume", "adjclose"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return len(rows)


def _estimate_cost(client, plan: PullPlan) -> float:
    start = datetime.fromisoformat(plan.start_iso).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(plan.end_iso).replace(tzinfo=timezone.utc)
    return float(
        client.metadata.get_cost(
            dataset=plan.dataset,
            schema=plan.schema,
            symbols=[plan.ticker],
            stype_in=plan.stype_in,
            start=start,
            end=end,
        )
    )


def _download_one(client, plan: PullPlan, *, tmpdir: Path) -> tuple[int, str]:
    """Download ohlcv-1d for one ticker and convert to CSV. Returns (n_rows, status)."""
    start = datetime.fromisoformat(plan.start_iso).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(plan.end_iso).replace(tzinfo=timezone.utc)
    dbn_path = tmpdir / f"{plan.ticker}.{plan.schema}.dbn.zst"
    try:
        client.timeseries.get_range(
            dataset=plan.dataset,
            schema=plan.schema,
            symbols=[plan.ticker],
            stype_in=plan.stype_in,
            start=start,
            end=end,
            path=str(dbn_path),
        )
    except Exception as exc:  # noqa: BLE001
        return (0, _redact(f"download_failed: {exc!r}"))
    try:
        rows = _dbn_to_csv_rows(dbn_path, plan.ticker)
    except Exception as exc:  # noqa: BLE001
        return (0, _redact(f"dbn_parse_failed: {exc!r}"))
    finally:
        if dbn_path.exists():
            try:
                dbn_path.unlink()
            except OSError:
                pass
    if not rows:
        return (0, "no_bars_returned")
    n = write_csv(plan.output_path, rows)
    return (n, "downloaded")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "fetch_delisted_daily")
    p.add_argument("--config", default=str(REPO / "packages" / "equities_lane" / "config" / "historical_runner_benchmark.yaml"))
    p.add_argument("--dry-run", action="store_true", default=True, help="Plan only, no API key needed. (default)")
    p.add_argument("--confirm-purchase", action="store_true", help="Required for confirmed downloads.")
    p.add_argument("--max-total-cost-usd", type=float, default=None)
    p.add_argument("--max-tickers", type=int, default=None)
    p.add_argument("--override-operating-cap", action="store_true")
    p.add_argument("--override-hard-limit", action="store_true")
    p.add_argument("--manifest", default=None, help="Manifest path. Default: <output_root>/_fetch_summary.json")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    confirmed_download = bool(args.confirm_purchase)
    args.dry_run = not confirmed_download

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"config not found: {config_path}", file=sys.stderr)
        return 2
    cfg = load_seed_config(config_path)
    plans = build_pull_plan(cfg)

    block = _load_paid_block(cfg)
    output_root = Path(str(block.get("output_root", "data/equities/daily_delisted")))
    if not output_root.is_absolute():
        output_root = (REPO / output_root).resolve()
    manifest_path = Path(args.manifest) if args.manifest else (output_root / "_fetch_summary.json")

    summary: list[dict[str, Any]] = []
    n_planned = len(plans)
    n_executable = 0
    n_skipped = 0
    estimated_total = 0.0
    api_key_present = bool(os.environ.get("DATABENTO_API_KEY"))

    print(f"delisted daily pull: {n_planned} tickers; mode={'dry-run' if args.dry_run else 'confirmed-download'}; output_root={output_root}", flush=True)

    if not plans:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps({
            "mode": "dry_run" if args.dry_run else "confirmed_download",
            "databento_api_key_present": api_key_present,
            "n_planned": 0,
            "n_executable": 0,
            "n_skipped": 0,
            "estimated_total_cost_usd": 0.0,
            "output_root": str(output_root),
            "ticker_records": [],
        }, indent=2), encoding="utf-8")
        print(f"no delisted tickers in config; empty manifest at {manifest_path}")
        return 0

    if not confirmed_download:
        for plan in plans:
            record = {
                "ticker": plan.ticker,
                "cohort": plan.cohort,
                "target_year": plan.target_year,
                "start_utc": plan.start_iso,
                "end_utc": plan.end_iso,
                "dataset": plan.dataset,
                "schema": plan.schema,
                "stype_in": plan.stype_in,
                "estimated_path": str(plan.output_path),
                "status": "dry_run_no_estimate_no_api_key" if not api_key_present else "dry_run_planned",
            }
            summary.append(record)
            print(f"[plan] {plan.ticker:<6} {plan.start_iso} -> {plan.end_iso}  {plan.dataset} {plan.schema}", flush=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps({
            "mode": "dry_run",
            "databento_api_key_present": api_key_present,
            "n_planned": n_planned,
            "n_executable": 0,
            "n_skipped": 0,
            "estimated_total_cost_usd": 0.0,
            "output_root": str(output_root),
            "ticker_records": summary,
        }, indent=2), encoding="utf-8")
        print(f"dry-run manifest: {manifest_path}")
        return 0

    if not api_key_present:
        print("refusing: confirmed download requires DATABENTO_API_KEY in env", file=sys.stderr)
        return 4
    if db is None:
        print("refusing: databento library not installed", file=sys.stderr)
        return 5

    client = db.Historical(os.environ["DATABENTO_API_KEY"])

    ticker_records: list[dict[str, Any]] = []
    running_total = 0.0
    with tempfile.TemporaryDirectory(prefix="delisted_daily_") as tmp:
        tmpdir = Path(tmp)
        for plan in plans:
            if args.max_tickers is not None and n_executable >= args.max_tickers:
                ticker_records.append({
                    "ticker": plan.ticker,
                    "cohort": plan.cohort,
                    "start_utc": plan.start_iso,
                    "end_utc": plan.end_iso,
                    "dataset": plan.dataset,
                    "schema": plan.schema,
                    "status": "skipped_max_tickers",
                    "limit": args.max_tickers,
                })
                n_skipped += 1
                continue

            try:
                cost = _estimate_cost(client, plan)
            except Exception as exc:  # noqa: BLE001
                ticker_records.append({
                    "ticker": plan.ticker,
                    "cohort": plan.cohort,
                    "start_utc": plan.start_iso,
                    "end_utc": plan.end_iso,
                    "dataset": plan.dataset,
                    "schema": plan.schema,
                    "status": "estimate_failed",
                    "error": _redact(f"{exc!r}"),
                })
                n_skipped += 1
                continue

            if args.max_total_cost_usd is not None and (running_total + cost) > args.max_total_cost_usd:
                ticker_records.append({
                    "ticker": plan.ticker,
                    "cohort": plan.cohort,
                    "start_utc": plan.start_iso,
                    "end_utc": plan.end_iso,
                    "dataset": plan.dataset,
                    "schema": plan.schema,
                    "status": "skipped_total_cost_cap",
                    "would_be_cost_usd": round(cost, 4),
                    "running_total_usd": round(running_total, 4),
                    "cap_usd": args.max_total_cost_usd,
                })
                n_skipped += 1
                continue

            hard_limit = 10.0
            if cost > hard_limit and not args.override_hard_limit:
                ticker_records.append({
                    "ticker": plan.ticker,
                    "cohort": plan.cohort,
                    "start_utc": plan.start_iso,
                    "end_utc": plan.end_iso,
                    "dataset": plan.dataset,
                    "schema": plan.schema,
                    "status": "skipped_above_hard_limit",
                    "cost_estimate_usd": round(cost, 4),
                    "hard_limit_usd": hard_limit,
                })
                n_skipped += 1
                continue

            n_bars, status = _download_one(client, plan, tmpdir=tmpdir)
            running_total += cost
            estimated_total += cost
            ticker_records.append({
                "ticker": plan.ticker,
                "cohort": plan.cohort,
                "start_utc": plan.start_iso,
                "end_utc": plan.end_iso,
                "dataset": plan.dataset,
                "schema": plan.schema,
                "n_bars": n_bars,
                "cost_estimate_usd": round(cost, 4),
                "running_total_usd": round(running_total, 4),
                "status": status,
            })
            n_executable += 1
            print(f"[{n_executable:>2}/{n_planned}] {plan.ticker:<6} {status:<24} {n_bars} bars ~${cost:.4f}", flush=True)

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({
        "mode": "confirmed_download",
        "databento_api_key_present": True,
        "n_planned": n_planned,
        "n_executable": n_executable,
        "n_skipped": n_skipped,
        "estimated_total_cost_usd": round(estimated_total, 4),
        "output_root": str(output_root),
        "ticker_records": ticker_records,
    }, indent=2), encoding="utf-8")
    print(f"confirmed download manifest: {manifest_path}; estimated_total=${estimated_total:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
