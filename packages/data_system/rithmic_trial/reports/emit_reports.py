from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..latency.percentile_stats import stats_by_key, stats_us


def _local_mono_ns(ev: dict[str, Any]) -> int | None:
    v = ev.get("local_monotonic_receive_ns")
    if v is not None:
        return int(v)
    v = ev.get("local_receive_timestamp_ns")
    return int(v) if v is not None else None


def _local_wall_ns(ev: dict[str, Any]) -> int | None:
    v = ev.get("local_receive_timestamp_ns")
    return int(v) if v is not None else None


def build_latency_profile(events: list[dict[str, Any]]) -> dict[str, Any]:
    feed_latencies_us: list[float] = []
    order_rtts_us: list[float] = []
    submit_ts: dict[str, int] = {}
    paired_records: list[dict[str, Any]] = []

    for ev in events:
        et = ev.get("event_type")
        local_ts = _local_mono_ns(ev)
        local_wall_ts = _local_wall_ns(ev)
        exch_ts = ev.get("exchange_timestamp_ns")
        if local_wall_ts is not None and exch_ts is not None:
            feed_latencies_us.append((int(local_wall_ts) - int(exch_ts)) / 1000.0)
        oid = ev.get("order_id")
        if not oid:
            continue
        oid_s = str(oid)
        if et == "order_submit" and local_ts is not None:
            submit_ts[oid_s] = int(local_ts)
        if et in ("order_ack", "ack") and local_ts is not None and oid_s in submit_ts:
            delta_us = (int(local_ts) - submit_ts[oid_s]) / 1000.0
            order_rtts_us.append(delta_us)
            paired_records.append(
                {
                    "order_id": oid_s,
                    "symbol": ev.get("symbol"),
                    "order_type": ev.get("order_type") or ev.get("type") or "unknown",
                    "market_state": ev.get("market_state") or "unknown",
                    "session_tag": ev.get("session_tag") or "regular",
                    "submit_to_ack_us": delta_us,
                }
            )

    feed = stats_us(feed_latencies_us)
    orders = stats_us(order_rtts_us)
    order_rtt_ms = (orders["avg_us"] / 1000.0) if orders.get("avg_us") is not None else None

    dimensions = {
        "by_symbol": stats_by_key(paired_records, "symbol", lambda r: r.get("submit_to_ack_us")),
        "by_order_type": stats_by_key(paired_records, "order_type", lambda r: r.get("submit_to_ack_us")),
        "by_market_state": stats_by_key(paired_records, "market_state", lambda r: r.get("submit_to_ack_us")),
        "by_session": stats_by_key(paired_records, "session_tag", lambda r: r.get("submit_to_ack_us")),
    }

    return {
        "status": "pass" if feed_latencies_us or order_rtts_us else "warn",
        "feed_latency_us": feed,
        "order_submit_to_ack_us": orders,
        "order_rtt_ms": order_rtt_ms,
        "paired_count": len(order_rtts_us),
        "dimensions": dimensions,
        "limitations": [] if order_rtts_us else ["No order ack events captured yet"],
    }


def build_paper_order_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    profile = build_latency_profile(events)
    orders = profile.get("order_submit_to_ack_us") or {}
    count = int(orders.get("count") or 0)
    return {
        "paired_count": count,
        "meets_acceptance_threshold": count >= 1000,
        "order_submit_to_ack_us": orders,
        "dimensions": profile.get("dimensions"),
    }


def build_rithmic_test_order_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    profile = build_latency_profile(events)
    orders = profile.get("order_submit_to_ack_us") or {}
    return {
        "paired_count": int(orders.get("count") or 0),
        "order_submit_to_ack_us": orders,
        "dimensions": profile.get("dimensions"),
        "latency_profile_status": profile.get("status"),
    }


def _write(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


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
    waterfall_records_path: Path | None = None,
) -> dict[str, str]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    latency = build_latency_profile(events)
    capture_env = str(manifest.get("capture_environment") or "").lower()
    use_legacy_paper_summary = "paper" in capture_env or waterfall_records_path is not None
    order_summary_name = (
        "paper_order_summary.json"
        if use_legacy_paper_summary
        else "rithmic_test_order_summary.json"
    )
    order_summary = (
        build_paper_order_summary(events)
        if use_legacy_paper_summary
        else build_rithmic_test_order_summary(events)
    )

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
        order_summary_name: _write(reports_dir / order_summary_name, order_summary),
        "hftbacktest_conversion_report.json": _write(
            reports_dir / "hftbacktest_conversion_report.json",
            conversion,
        ),
    }

    if waterfall_records_path is not None and waterfall_records_path.is_file():
        from .waterfall import write_waterfall_report

        wf_path = reports_dir / "latency_waterfall.json"
        write_waterfall_report(waterfall_records_path, wf_path)
        paths["latency_waterfall.json"] = wf_path

    return {k: str(v) for k, v in paths.items()}
