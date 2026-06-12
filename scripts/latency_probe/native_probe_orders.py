"""Ingest native C++ rithmic_latency_probe JSONL samples for order-ack latency."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _load_jsonl_dir(repo_root: Path) -> tuple[list[float], list[str], list[str], list[str], int]:
    """Return (latencies_us, sample_files, run_ids, symbols, skipped_lines)."""
    baseline_root = repo_root / "data" / "latency_baselines"
    latencies: list[float] = []
    sample_files: list[str] = []
    run_ids_seen: list[str] = []
    symbols_seen: list[str] = []
    skipped = 0

    for jsonl_path in sorted(baseline_root.glob("*/*.jsonl")):
        rel = str(jsonl_path.relative_to(repo_root)).replace("\\", "/")
        file_contributed = False
        try:
            text = jsonl_path.read_text(encoding="utf-8")
        except OSError:
            skipped += 1
            continue
        for raw_line in text.splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                obj: dict[str, Any] = json.loads(raw_line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(obj, dict):
                skipped += 1
                continue

            try:
                order_action = obj.get("order_action")
                success = obj.get("success")
                send_to_ack_us = obj.get("send_to_ack_us")
                raw_ts = obj.get("raw_timestamps") or {}
                order_send_ts = raw_ts.get("order_send_ts")
                ack_received_ts = raw_ts.get("ack_received_ts")

                if order_action != "new":
                    skipped += 1
                    continue
                if not success:
                    skipped += 1
                    continue
                if not isinstance(send_to_ack_us, (int, float)):
                    skipped += 1
                    continue
                if not isinstance(order_send_ts, (int, float)) or order_send_ts <= 0:
                    skipped += 1
                    continue
                if not isinstance(ack_received_ts, (int, float)) or ack_received_ts <= 0:
                    skipped += 1
                    continue
            except Exception:
                skipped += 1
                continue

            latencies.append(float(send_to_ack_us))
            if not file_contributed:
                sample_files.append(rel)
                file_contributed = True

            run_id = obj.get("run_id")
            if isinstance(run_id, str) and run_id and run_id not in run_ids_seen:
                run_ids_seen.append(run_id)

            symbol = obj.get("symbol")
            if isinstance(symbol, str) and symbol and symbol not in symbols_seen:
                symbols_seen.append(symbol)

    return latencies, sample_files, run_ids_seen, symbols_seen, skipped


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Linear interpolation percentile (same method as numpy default)."""
    n = len(sorted_vals)
    if n == 0:
        return float("nan")
    if n == 1:
        return sorted_vals[0]
    idx = pct / 100.0 * (n - 1)
    lo = int(idx)
    hi = lo + 1
    frac = idx - lo
    if hi >= n:
        return sorted_vals[-1]
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


def collect_native_probe_orders(repo_root: Path) -> dict[str, Any]:
    """Scan data/latency_baselines/*/*.jsonl for native C++ probe order-ack samples.

    Returns a dict with keys:
        paired_count, order_submit_to_ack_us, sample_files, runs, symbols, skipped_lines
    """
    latencies, sample_files, run_ids, symbols, skipped = _load_jsonl_dir(repo_root)
    n = len(latencies)

    if n == 0:
        return {
            "paired_count": 0,
            "order_submit_to_ack_us": {
                "count": 0,
                "p50_us": None,
                "p90_us": None,
                "p99_us": None,
                "p999_us": None,
            },
            "sample_files": sample_files,
            "runs": run_ids,
            "symbols": symbols,
            "skipped_lines": skipped,
        }

    sv = sorted(latencies)
    return {
        "paired_count": n,
        "order_submit_to_ack_us": {
            "count": n,
            "p50_us": _percentile(sv, 50.0),
            "p90_us": _percentile(sv, 90.0),
            "p99_us": _percentile(sv, 99.0),
            "p999_us": _percentile(sv, 99.9),
        },
        "sample_files": sample_files,
        "runs": run_ids,
        "symbols": symbols,
        "skipped_lines": skipped,
    }
