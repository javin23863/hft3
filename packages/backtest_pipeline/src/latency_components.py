"""HftBacktest three-component latency band schema and resolution helpers."""

from __future__ import annotations

from typing import Any

CRITICAL_BANDS = (
    "feed_latency_us",
    "new_send_to_exchange_us",
    "new_exchange_to_ack_us",
    "cancel_send_to_exchange_us",
    "cancel_exchange_to_ack_us",
)

LOCAL_BANDS = (
    "tick_to_send_us",
    "cancel_decision_to_send_us",
)

ROUND_TRIP_BANDS = (
    "new_send_to_ack_us",
)

MEASUREMENT_STATUSES = frozenset({"MEASURED", "INFERRED", "OPEN", "UNMEASURED"})


def distribution_from_stats(stats: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize native probe stats into a distribution object."""
    if not isinstance(stats, dict):
        return None
    count = int(stats.get("count") or 0)
    if count <= 0:
        return None
    out: dict[str, Any] = {"count": count}
    for key in ("min_us", "mean_us", "p50_us", "p90_us", "p95_us", "p99_us", "p99_9_us", "max_us"):
        val = stats.get(key)
        if isinstance(val, (int, float)):
            out[key] = float(val)
    return out


def distribution_to_ms(dist: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(dist, dict):
        return None
    out: dict[str, Any] = {"count": dist.get("count", 0)}
    for key in ("min_us", "mean_us", "p50_us", "p90_us", "p95_us", "p99_us", "p99_9_us", "max_us"):
        val = dist.get(key)
        if isinstance(val, (int, float)):
            ms_key = key.replace("_us", "_ms")
            out[ms_key] = float(val) / 1000.0
    return out


def build_new_send_to_ack_from_live_stats(live_stats: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build canonical new_send_to_ack distribution from order_submit_to_ack_us stats."""
    dist_us = distribution_from_stats(live_stats)
    if dist_us is None:
        return None
    dist_ms = distribution_to_ms(dist_us)
    return {
        "metric": "new_send_to_ack",
        "unit_primary": "us",
        "us": dist_us,
        "ms": dist_ms,
        "source": "rithmic_latency_probe_native_cpp",
        "hftbacktest_component": "order_entry_plus_order_response_combined",
        "note": "Full local send to local ack; split into entry/response when CC-3 samples exist.",
    }


def resolve_new_send_to_ack_ms(summary: dict[str, Any]) -> tuple[float | None, dict[str, Any] | None, str]:
    """Return (p99_ms, full_distribution, source_label). Prefers new_send_to_ack_ms over legacy."""
    block = summary.get("new_send_to_ack_ms")
    if isinstance(block, dict):
        ms = block.get("ms") if isinstance(block.get("ms"), dict) else block
        p99 = ms.get("p99_ms") if isinstance(ms, dict) else None
        if isinstance(p99, (int, float)):
            return float(p99), block, "new_send_to_ack_ms.authoritative"
    legacy = summary.get("live_order_ack_p99_ms")
    if isinstance(legacy, (int, float)):
        return float(legacy), None, "live_order_ack_p99_ms.deprecated"
    live = summary.get("live_order_latency") or {}
    stats = (summary.get("native_probe_orders_live") or {}).get("order_submit_to_ack_us")
    if not isinstance(stats, dict):
        stats = None
    built = build_new_send_to_ack_from_live_stats(stats)
    if built and isinstance(built.get("ms"), dict):
        p99 = built["ms"].get("p99_ms")
        if isinstance(p99, (int, float)):
            return float(p99), built, "derived_from_native_probe"
    return None, None, "unmeasured"


def default_component_bands(*, live_placement: dict[str, Any] | None = None) -> dict[str, Any]:
    """Seed component_bands with known measured/open status from existing artifacts."""
    live = live_placement or {}
    offensive = live.get("offensive_us") or {}
    defensive = live.get("defensive_us") or {}
    round_trip = live.get("round_trip_ms") or {}

    def band(name: str, status: str, dist: dict[str, Any] | None = None, note: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "metric": name,
            "measurement_status": status,
            "hftbacktest_component": _hftbacktest_component(name),
        }
        if dist:
            payload["distribution_us"] = dist
        if note:
            payload["note"] = note
        return payload

    tick_p99 = offensive.get("tick_to_send_p99")
    cancel_send_p99 = defensive.get("cancel_to_send_p99")
    send_ack_p50_ms = round_trip.get("send_to_ack_p50")
    send_ack_p99_ms = round_trip.get("send_to_ack_p99")

    bands: dict[str, Any] = {
        "feed_latency_us": band(
            "feed_latency_us",
            "OPEN",
            note="Instrument probe v3 MD path; CC-2 campaign pending",
        ),
        "new_send_to_exchange_us": band(
            "new_send_to_exchange_us",
            "OPEN",
            note="Requires req_ts + calibrated exch_ts; CC-3 campaign pending",
        ),
        "new_exchange_to_ack_us": band(
            "new_exchange_to_ack_us",
            "OPEN",
            note="Requires calibrated exch_ts + resp_ts; CC-3 campaign pending",
        ),
        "cancel_send_to_exchange_us": band(
            "cancel_send_to_exchange_us",
            "OPEN",
            note="CC-4 campaign pending",
        ),
        "cancel_exchange_to_ack_us": band(
            "cancel_exchange_to_ack_us",
            "UNMEASURED",
            note="Live placement test: all 25 cancel ack timeouts",
        ),
        "tick_to_send_us": band(
            "tick_to_send_us",
            "MEASURED" if tick_p99 is not None else "OPEN",
            {"p50_us": offensive.get("tick_to_send_p50"), "p99_us": tick_p99}
            if tick_p99 is not None
            else None,
        ),
        "cancel_decision_to_send_us": band(
            "cancel_decision_to_send_us",
            "MEASURED" if cancel_send_p99 is not None else "OPEN",
            {"p50_us": defensive.get("cancel_to_send_p50"), "p99_us": cancel_send_p99}
            if cancel_send_p99 is not None
            else None,
        ),
        "new_send_to_ack_us": band(
            "new_send_to_ack_us",
            "MEASURED" if send_ack_p99_ms is not None else "OPEN",
            {
                "p50_us": send_ack_p50_ms * 1000.0 if send_ack_p50_ms else None,
                "p99_us": send_ack_p99_ms * 1000.0 if send_ack_p99_ms else None,
            }
            if send_ack_p99_ms is not None
            else None,
            note="Placement test n=25; ack campaign n=200 in new_send_to_ack_ms",
        ),
        "fill_exchange_to_local_us": band("fill_exchange_to_local_us", "OPEN"),
        "modify_send_to_exchange_us": band("modify_send_to_exchange_us", "OPEN"),
        "modify_exchange_to_ack_us": band("modify_exchange_to_ack_us", "OPEN"),
        "reject_or_throttle_to_response_us": band("reject_or_throttle_to_response_us", "OPEN"),
        "send_queue_delay_us": band(
            "send_queue_delay_us",
            "INFERRED",
            {"p99_us": offensive.get("tick_to_send_trigger_p99")},
            note="Partial: tick_to_send_trigger_us approximates decision to SDK entry",
        ),
    }
    return bands


def _hftbacktest_component(metric: str) -> str:
    mapping = {
        "feed_latency_us": "feed",
        "new_send_to_exchange_us": "order_entry",
        "new_exchange_to_ack_us": "order_response",
        "cancel_send_to_exchange_us": "order_entry",
        "cancel_exchange_to_ack_us": "order_response",
        "new_send_to_ack_us": "order_entry_plus_order_response_combined",
        "tick_to_send_us": "local_only",
        "cancel_decision_to_send_us": "local_only",
        "fill_exchange_to_local_us": "order_response",
    }
    return mapping.get(metric, "local_or_unknown")


def critical_bands_measured(bands: dict[str, Any]) -> bool:
    for name in CRITICAL_BANDS:
        status = (bands.get(name) or {}).get("measurement_status")
        if status != "MEASURED":
            return False
    return True
