"""CHI404 measured latency summary for backtest replay (colo bare metal)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[3]
DEFAULT_CHI404_SUMMARY = _REPO / "runtime" / "latency_reports" / "latency_summary.json"
DEFAULT_LATENCY_TRUTH = _REPO / "runtime" / "latency_reports" / "latency_truth.json"

PAPER_ORDER_MIN_PAIRED = 1000

BACKTEST_LATENCY_NOTE_LIVE = (
    "Live Rithmic 01 order submit→ack p99 from native C++ rithmic_latency_probe on CHI404 colo"
)
BACKTEST_LATENCY_NOTE_PAPER = (
    "Paper order submit→ack p99 from native C++ rithmic_latency_probe on CHI404 colo"
)
BACKTEST_LATENCY_NOTE_UNMEASURED = (
    "Paper order submit→ack not measured; pass --latency-ms or run "
    "scripts/chi404_run_paper_latency_sweep.sh on CHI404. "
    "TCP connect is network health only — not used for execution latency."
)
# Backward-compatible alias for scripts that reference a single note string.
BACKTEST_LATENCY_NOTE = BACKTEST_LATENCY_NOTE_UNMEASURED
LATENCY_BAND_MIN_MS = 0.5
LATENCY_BAND_MAX_MS = 10.0


def validate_replay_latency_ms(latency_ms: float, *, source: str) -> float:
    """Fail loud if latency is outside blueprint-mandated replay band."""
    ms = float(latency_ms)
    if not (LATENCY_BAND_MIN_MS <= ms <= LATENCY_BAND_MAX_MS):
        raise ValueError(
            f"Replay latency {ms} ms from {source} outside BLUEPRINT band "
            f"[{LATENCY_BAND_MIN_MS}, {LATENCY_BAND_MAX_MS}] ms"
        )
    return ms


def resolve_offensive_tick_to_send_us(truth_path: Path = DEFAULT_LATENCY_TRUTH) -> float:
    """Owner-measured offensive tick->send p99 (us) from latency_truth.json.

    This is OUR fire path (tick observed -> order on the wire) and must be
    ADDED to the replay entry leg; the round-trip ack campaign only measures
    send->ack. Fails loud when unmeasured — never a silent zero.
    """
    truth_path = Path(truth_path)
    if not truth_path.is_file():
        raise FileNotFoundError(f"latency truth missing: {truth_path}")
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    block = truth.get("live_placement") if isinstance(truth.get("live_placement"), dict) else truth
    offensive = block.get("offensive_us") or {}
    p99 = offensive.get("tick_to_send_p99")
    if not isinstance(p99, (int, float)) or float(p99) <= 0:
        raise ValueError(f"offensive_us.tick_to_send_p99 unmeasured in {truth_path}")
    return float(p99)


def resolve_order_ack_ms(summary: dict[str, Any]) -> tuple[float | None, bool, str]:
    """Return (order_ack_p99_ms, measured, source_label). Prefers new_send_to_ack over legacy."""
    try:
        from backtest_pipeline.src.latency_components import resolve_new_send_to_ack_ms

        p99, _dist, source = resolve_new_send_to_ack_ms(summary)
        if p99 is not None:
            measured = (
                source.startswith("new_send_to_ack")
                or source.startswith("live_order")
                or source == "derived_from_native_probe"
            )
            return p99, measured, source
    except ImportError:
        pass

    live = summary.get("live_order_latency") or {}
    live_p99 = summary.get("live_order_ack_p99_ms")
    if live.get("authoritative") and isinstance(live_p99, (int, float)):
        return float(live_p99), True, "live_order_latency.authoritative"
    if live.get("preliminary") and isinstance(live_p99, (int, float)):
        return float(live_p99), True, "live_order_latency.preliminary"

    # Do not fall back to paper when live samples exist (HFT requires real wire).
    if int(live.get("paired_count") or 0) > 0 and isinstance(live_p99, (int, float)):
        return float(live_p99), True, "live_order_latency.partial"

    paper = summary.get("paper_order_latency") or {}
    if paper.get("measured") and isinstance(summary.get("order_ack_p99_ms"), (int, float)):
        return float(summary["order_ack_p99_ms"]), True, "paper_order_latency.authoritative"

    appendix = summary.get("trial_order_ack_appendix") or {}
    if appendix.get("status") == "ok" and appendix.get("authoritative"):
        ms = appendix.get("order_ack_p99_ms")
        if isinstance(ms, (int, float)):
            return float(ms), True, "trial_order_ack_appendix.authoritative"

    stats = appendix.get("order_submit_to_ack_us") or {}
    count = int(stats.get("count") or 0)
    p99_us = stats.get("p99_us")
    if (
        appendix.get("status") == "ok"
        and appendix.get("authoritative")
        and count >= PAPER_ORDER_MIN_PAIRED
        and isinstance(p99_us, (int, float))
    ):
        return float(p99_us) / 1000.0, True, "trial_order_ack_appendix.authoritative"

    return None, False, "unmeasured"


def load_chi404_speed(summary_path: Path) -> dict[str, Any]:
    if not summary_path.is_file():
        raise FileNotFoundError(
            f"CHI404 latency summary missing: {summary_path}. "
            "Run scripts/latency_probe/run_all.sh on CHI404 or chi404_sync_trial_data.sh."
        )
    s = json.loads(summary_path.read_text(encoding="utf-8"))
    network = s.get("network") or {}
    rithmic_tcp = network.get("rithmic_tcp_65000") or {}
    gateway = network.get("gateway_ping") or {}
    cyclictest = s.get("cyclictest") or {}
    trial = s.get("trial_order_ack_appendix") or {}
    network_health = s.get("network_health") or {}

    rithmic_tcp_p99_ms = rithmic_tcp.get("p99_ms")
    order_ack_ms, measured, ack_source = resolve_order_ack_ms(s)

    payload: dict[str, Any] = {
        "probe_run_id": s.get("run_id"),
        "probe_timestamp_utc": s.get("timestamp_utc"),
        "source": s.get("authoritative_source"),
        "cpu_loaded_p99_us": cyclictest.get("max_p99_us"),
        "gateway_ping_p99_ms": gateway.get("p99_ms"),
        "rithmic_tcp_65000_p99_ms": float(rithmic_tcp_p99_ms)
        if isinstance(rithmic_tcp_p99_ms, (int, float))
        else None,
        "network_health_only_tcp_p99_ms": network_health.get("rithmic_tcp_65000_p99_ms"),
        "network_worst_p99_us": s.get("network_p99_us"),
        "network_worst_source": s.get("network_p99_worst_source"),
        "order_ack_p99_ms": order_ack_ms,
        "order_ack_source": ack_source,
        "trial_order_ack_p99_ms": trial.get("order_ack_p99_ms"),
        "trial_order_ack_status": trial.get("status"),
        "order_ack_measured": measured,
        "paper_order_latency": s.get("paper_order_latency"),
    }

    if measured and order_ack_ms is not None:
        payload["backtest_latency_ms"] = order_ack_ms
        payload["backtest_latency_source"] = ack_source
        if ack_source == "live_order_latency.authoritative":
            payload["backtest_latency_note"] = BACKTEST_LATENCY_NOTE_LIVE
        else:
            payload["backtest_latency_note"] = BACKTEST_LATENCY_NOTE_PAPER
        payload["live_order_latency"] = s.get("live_order_latency")
        payload["live_order_ack_p99_ms"] = s.get("live_order_ack_p99_ms")
    else:
        payload["backtest_latency_ms"] = None
        payload["backtest_latency_source"] = None
        payload["backtest_latency_note"] = BACKTEST_LATENCY_NOTE_UNMEASURED

    return payload


def resolve_replay_latency_ms(
    *,
    latency_ms: float | None,
    chi404_summary: Path = DEFAULT_CHI404_SUMMARY,
) -> tuple[float, str, dict[str, Any] | None]:
    """Return (latency_ms, source_label, chi404_payload_or_none) for replay scripts."""
    if latency_ms is not None:
        ms = validate_replay_latency_ms(float(latency_ms), source="CLI --latency-ms")
        return ms, "CLI --latency-ms", None

    chi404 = load_chi404_speed(chi404_summary.resolve())
    if not chi404.get("order_ack_measured") or chi404.get("backtest_latency_ms") is None:
        raise ValueError(
            f"{BACKTEST_LATENCY_NOTE_UNMEASURED} "
            f"Summary: {chi404_summary.resolve()}"
        )
    ms = validate_replay_latency_ms(
        float(chi404["backtest_latency_ms"]),
        source=str(chi404.get("backtest_latency_source", "measured order ack")),
    )
    return ms, str(chi404.get("backtest_latency_source", "CHI404 measured order ack")), chi404


def build_latency_model_from_summary(
    *,
    regime: str,
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Build a latency_model.json-compatible dict from a parsed latency_summary."""
    p99_ms, measured, ack_source = resolve_order_ack_ms(summary)
    if not measured or p99_ms is None:
        raise ValueError(BACKTEST_LATENCY_NOTE_UNMEASURED)

    new_send = summary.get("new_send_to_ack_ms") or {}
    ms_block = new_send.get("ms") if isinstance(new_send, dict) else {}
    p50_ms = ms_block.get("p50_ms") if isinstance(ms_block, dict) else None
    p90_ms = ms_block.get("p90_ms") if isinstance(ms_block, dict) else None

    entry_ms = float(p50_ms) if isinstance(p50_ms, (int, float)) else float(p99_ms) / 2.0
    resp_ms = float(p99_ms) - entry_ms if float(p99_ms) > entry_ms else float(p99_ms) / 2.0
    if regime == "fast" and isinstance(p50_ms, (int, float)):
        entry_ms = resp_ms = float(p50_ms) / 2.0
    elif regime == "normal" and isinstance(p90_ms, (int, float)):
        entry_ms = resp_ms = float(p90_ms) / 2.0
    elif regime == "stress":
        entry_ms = resp_ms = float(p99_ms) / 2.0
    elif regime == "extreme":
        p999 = ms_block.get("p99_9_ms") or ms_block.get("max_ms")
        if isinstance(p999, (int, float)):
            entry_ms = resp_ms = float(p999) / 2.0

    return {
        "latency_model_family": "ConstantLatency",
        "latency_regime": regime,
        "feed_latency_source": "open_pending_cc2_campaign",
        "order_entry_latency_source": ack_source,
        "order_response_latency_source": ack_source,
        "latency_units": "milliseconds",
        "latency_source_authority": "hft3_native_cpp_rithmic_latency_probe",
        "latency_proxy_status": "measured_partial",
        "latency_component_mapping": {
            "feed_latency": "feed_latency_us from probe v3 CC-2",
            "order_entry_latency": "new_send_to_exchange_us or symmetric split of new_send_to_ack",
            "order_response_latency": "new_exchange_to_ack_us or symmetric split of new_send_to_ack",
        },
        "feed_latency_ms": None,
        "order_entry_latency_ms": entry_ms,
        "order_response_latency_ms": resp_ms,
        "latency_p50_ms": float(p50_ms) if isinstance(p50_ms, (int, float)) else None,
        "latency_p90_ms": float(p90_ms) if isinstance(p90_ms, (int, float)) else None,
        "latency_p99_ms": float(p99_ms),
        "order_response_latency_modeled": True,
        "native_latency_probe_host": "CHI404",
        "note": "Symmetric split until CC-3 IntpOrderLatency samples populate entry/response separately.",
    }


def enrich_latency_model_probe_evidence(
    model: dict[str, Any],
    *,
    chi404_summary: Path,
) -> dict[str, Any]:
    """Attach required native-probe realism evidence fields to a latency model."""
    from backtest_pipeline.src.hftbacktest_realism import (
        compute_latency_probe_artifact_hash,
        compute_latency_value_or_sample_hash,
    )

    summary_path = chi404_summary.resolve()
    artifact_rel = str(summary_path.relative_to(_REPO)).replace("\\", "/")
    enriched = dict(model)
    enriched["native_latency_probe_artifact"] = artifact_rel
    enriched["native_latency_probe_status"] = "provided"
    enriched["native_latency_probe_provenance"] = "hft3_native_cpp_rithmic_latency_probe"
    enriched["native_latency_probe_host"] = "CHI404"
    if summary_path.is_file():
        enriched["native_latency_probe_artifact_hash"] = compute_latency_probe_artifact_hash(summary_path)
    enriched["latency_value_or_sample_hash"] = compute_latency_value_or_sample_hash(enriched)
    return enriched


def resolve_latency_model(
    *,
    regime: str = "stress",
    chi404_summary: Path = DEFAULT_CHI404_SUMMARY,
) -> dict[str, Any]:
    """Build a latency_model.json-compatible dict from measured CHI404 artifacts."""
    summary_path = chi404_summary.resolve()
    if not summary_path.is_file():
        raise FileNotFoundError(f"CHI404 latency summary missing: {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    regime_dir = _REPO / "reports" / "latency_baselines" / "live_r01_chicago"
    regime_path = regime_dir / f"latency_model_{regime}.json"
    if regime_path.is_file():
        model = json.loads(regime_path.read_text(encoding="utf-8"))
        if isinstance(model, dict) and model.get("latency_model_family"):
            if not model.get("latency_value_or_sample_hash"):
                model = enrich_latency_model_probe_evidence(model, chi404_summary=summary_path)
            return model

    model = build_latency_model_from_summary(regime=regime, summary=summary)
    return enrich_latency_model_probe_evidence(model, chi404_summary=summary_path)
