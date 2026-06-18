"""HftBacktest three-component latency band schema and resolution helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

MIN_CC_SAMPLE_COUNT = 50

_CC_SUMMARY_PREFIXES: dict[str, str] = {
    "cc2": "cc2_feed_",
    "cc3": "cc3_new_decomp_",
    "cc4": "cc4_cancel_",
}

_METRIC_CC_SOURCES: dict[str, tuple[str, ...]] = {
    "feed_latency_us": ("cc3", "cc2"),
    "new_send_to_exchange_us": ("cc3",),
    "new_exchange_to_ack_us": ("cc3",),
    "cancel_send_to_exchange_us": ("cc4",),
    "cancel_exchange_to_ack_us": ("cc4",),
}

_RUN_SUFFIX_RE = re.compile(r"_(\d{8}T\d{6}Z)_summary\.json$")

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


def _cc_summary_sort_key(path: Path) -> str:
    match = _RUN_SUFFIX_RE.search(path.name)
    if match:
        return match.group(1)
    return path.name


def find_latest_cc_summary(repo_root: Path, prefix: str) -> Path | None:
    """Return newest CC summary JSON for a campaign prefix under reports/latency_baselines/."""
    baselines = repo_root / "reports" / "latency_baselines"
    if not baselines.is_dir():
        return None
    candidates = sorted(
        baselines.glob(f"{prefix}*_summary.json"),
        key=_cc_summary_sort_key,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load_cc_summaries(repo_root: Path) -> dict[str, dict[str, Any]]:
    """Load latest cc2/cc3/cc4 summary JSON objects keyed by campaign id."""
    loaded: dict[str, dict[str, Any]] = {}
    repo_root = Path(repo_root)
    for cc_key, prefix in _CC_SUMMARY_PREFIXES.items():
        path = find_latest_cc_summary(repo_root, prefix)
        if path is None:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            loaded[cc_key] = data
    return loaded


def extract_metric_stats(summary: dict[str, Any], metric: str) -> dict[str, Any] | None:
    """Read metric stats from summary.metrics or a top-level component block."""
    metrics = summary.get("metrics")
    if isinstance(metrics, dict):
        stats = metrics.get(metric)
        if isinstance(stats, dict):
            return stats
    block = summary.get(metric)
    if isinstance(block, dict) and "count" in block:
        return block
    us_block = summary.get(f"{metric}")
    if isinstance(us_block, dict) and isinstance(us_block.get("us"), dict):
        return us_block["us"]
    return None


def _regime_us_value(dist: dict[str, Any], regime: str) -> float | None:
    key_map = {
        "fast": "p50_us",
        "normal": "p90_us",
        "stress": "p99_us",
        "extreme": "p99_9_us",
    }
    key = key_map.get(regime, "p99_us")
    val = dist.get(key)
    if isinstance(val, (int, float)):
        return float(val)
    val = dist.get("p99_us")
    return float(val) if isinstance(val, (int, float)) else None


def enrich_latency_model_from_component_bands(
    model: dict[str, Any],
    bands: dict[str, Any],
    *,
    regime: str,
) -> dict[str, Any]:
    """Apply measured component_bands to a latency_model dict (feed/entry/response)."""
    out = dict(model)
    feed = bands.get("feed_latency_us") or {}
    entry = bands.get("new_send_to_exchange_us") or {}
    resp = bands.get("new_exchange_to_ack_us") or {}

    if feed.get("measurement_status") == "MEASURED":
        dist = feed.get("distribution_us") or {}
        feed_us = _regime_us_value(dist, regime)
        if feed_us is not None:
            out["feed_latency_ms"] = feed_us / 1000.0
            out["feed_latency_source"] = feed.get("source_run_id") or "cc3_feed_latency_us"
            mapping = dict(out.get("latency_component_mapping") or {})
            mapping["feed_latency"] = f"feed_latency_us from {feed.get('source_run_id', 'CC campaign')}"
            out["latency_component_mapping"] = mapping

    entry_dist = entry.get("distribution_us") if entry.get("measurement_status") == "MEASURED" else None
    resp_dist = resp.get("distribution_us") if resp.get("measurement_status") == "MEASURED" else None
    if isinstance(entry_dist, dict) and isinstance(resp_dist, dict):
        entry_us = _regime_us_value(entry_dist, regime)
        resp_us = _regime_us_value(resp_dist, regime)
        if entry_us is not None and resp_us is not None:
            out["order_entry_latency_ms"] = entry_us / 1000.0
            out["order_response_latency_ms"] = resp_us / 1000.0
            out["latency_p99_ms"] = (entry_us + resp_us) / 1000.0
            src = entry.get("source_run_id") or "cc3_new_decomp"
            out["order_entry_latency_source"] = src
            out["order_response_latency_source"] = src
            out["latency_proxy_status"] = "measured_decomposed"
            out["note"] = (
                f"CC-3 decomposed entry/response from {src}; "
                "symmetric split fallback no longer used for this regime."
            )
    return out


def merge_component_bands_from_cc_summaries(repo_root: Path, bands: dict[str, Any]) -> dict[str, Any]:
    """Merge CC-2/3/4 campaign summaries into component_bands measurement status."""
    loaded = load_cc_summaries(repo_root)
    merged = {name: dict(payload) for name, payload in bands.items()}
    cc4 = loaded.get("cc4") or {}
    cc4_run_id = cc4.get("run_id")

    for metric in CRITICAL_BANDS:
        base = dict(merged.get(metric) or {})
        base.setdefault("metric", metric)
        base.setdefault("hftbacktest_component", _hftbacktest_component(metric))

        stats: dict[str, Any] | None = None
        source_run_id: str | None = None
        for cc_key in _METRIC_CC_SOURCES.get(metric, ()):
            summary = loaded.get(cc_key)
            if not summary:
                continue
            candidate = extract_metric_stats(summary, metric)
            count = int((candidate or {}).get("count") or 0)
            if count > 0:
                stats = candidate
                source_run_id = str(summary.get("run_id") or cc_key)
                break

        if stats is not None and int(stats.get("count") or 0) >= MIN_CC_SAMPLE_COUNT:
            dist = distribution_from_stats(stats)
            base["measurement_status"] = "MEASURED"
            base["distribution_us"] = dist
            base["source_run_id"] = source_run_id
            base["sample_count"] = int(stats["count"])
            base["note"] = f"Measured by CC campaign {source_run_id} (n={int(stats['count'])})"
            merged[metric] = base
            continue

        if metric == "cancel_exchange_to_ack_us" and cc4_run_id:
            cc4_stats = extract_metric_stats(cc4, metric) or {}
            cc4_count = int(cc4_stats.get("count") or 0)
            base["measurement_status"] = "UNMEASURED"
            base["note"] = (
                f"CC-4 {cc4_run_id}: cancel_exchange_to_ack_us count={cc4_count} "
                "(live Rithmic 01 far-from-market; all cancel_ack_timeout in probe)"
            )
            if cc4_count > 0:
                base["sample_count"] = cc4_count
            base["source_run_id"] = cc4_run_id
            merged[metric] = base
            continue

        if metric == "cancel_send_to_exchange_us" and cc4_run_id:
            cc4_stats = extract_metric_stats(cc4, metric) or {}
            cc4_count = int(cc4_stats.get("count") or 0)
            base["measurement_status"] = "OPEN"
            base["source_run_id"] = cc4_run_id
            base["note"] = (
                f"CC-4 {cc4_run_id}: cancel_send_to_exchange_us count={cc4_count} "
                "(cancel_ack_timeout; local cancel_to_send_us measured in placement test)"
            )
            merged[metric] = base
            continue

        merged[metric] = base

    return merged


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
