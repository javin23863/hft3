from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any


def _write(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def build_latency_profile(events: list[dict[str, Any]]) -> dict[str, Any]:
    feed_latencies_us: list[float] = []
    order_rtts_us: list[float] = []
    submit_ts: dict[str, int] = {}

    for ev in events:
        et = ev.get("event_type")
        local_ts = ev.get("local_receive_timestamp_ns")
        exch_ts = ev.get("exchange_timestamp_ns")
        if local_ts is not None and exch_ts is not None:
            feed_latencies_us.append((int(local_ts) - int(exch_ts)) / 1000.0)
        oid = ev.get("order_id")
        if not oid:
            continue
        if et == "order_submit" and local_ts is not None:
            submit_ts[str(oid)] = int(local_ts)
        if et == "order_ack" and local_ts is not None and str(oid) in submit_ts:
            order_rtts_us.append(int(local_ts) - submit_ts[str(oid)])

    def _stats(vals: list[float]) -> dict[str, float | None]:
        if not vals:
            return {"count": 0, "min_us": None, "avg_us": None, "p99_us": None, "max_us": None}
        s = sorted(vals)
        p99_idx = max(0, int(len(s) * 0.99) - 1)
        return {
            "count": len(vals),
            "min_us": min(s),
            "avg_us": statistics.mean(s),
            "p99_us": s[p99_idx],
            "max_us": max(s),
        }

    feed = _stats(feed_latencies_us)
    orders = _stats([v / 1000.0 for v in order_rtts_us])  # ns -> us
    order_rtt_ms = (orders["avg_us"] / 1000.0) if orders["avg_us"] is not None else None

    return {
        "status": "pass" if feed_latencies_us or order_rtts_us else "warn",
        "feed_latency_us": feed,
        "order_submit_to_ack_us": orders,
        "order_rtt_ms": order_rtt_ms,
        "limitations": [] if order_rtts_us else ["No order ack events captured yet"],
    }


def emit_all_reports(
    reports_dir: Path,
    *,
    manifest: dict[str, Any],
    normalized_path: Path,
    events: list[dict[str, Any]],
    quality: dict[str, Any],
    book: dict[str, Any],
    conversion: dict[str, Any],
    schema_mapping: dict[str, Any],
) -> dict[str, str]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    latency = build_latency_profile(events)

    paths = {
        "data_capture_report.json": _write(
            reports_dir / "data_capture_report.json",
            {
                "status": "pass" if manifest.get("row_count", 0) > 0 else "fail",
                "manifest": manifest,
                "input_files": [manifest.get("raw_file")],
                "schema_version": manifest.get("schema_version"),
                "detected_event_types": manifest.get("detected_event_types", []),
                "missing_event_types": manifest.get("missing_event_types", []),
                "limitations": manifest.get("known_limitations", {}),
            },
        ),
        "schema_mapping_report.json": _write(
            reports_dir / "schema_mapping_report.json",
            {
                "status": "pass",
                **schema_mapping,
            },
        ),
        "data_quality_report.json": _write(
            reports_dir / "data_quality_report.json",
            {
                "input_files": [str(normalized_path)],
                **quality,
            },
        ),
        "book_reconstruction_report.json": _write(
            reports_dir / "book_reconstruction_report.json",
            book,
        ),
        "latency_profile.json": _write(
            reports_dir / "latency_profile.json",
            {
                "input_files": [str(normalized_path)],
                **latency,
            },
        ),
        "hftbacktest_conversion_report.json": _write(
            reports_dir / "hftbacktest_conversion_report.json",
            conversion,
        ),
    }
    return {k: str(v) for k, v in paths.items()}
