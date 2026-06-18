#!/usr/bin/env python3
"""Generate HftBacktest latency_model regime JSON artifacts from latency_summary.json."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import sys

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO / "packages") not in sys.path:
    sys.path.insert(0, str(_REPO / "packages"))

from backtest_pipeline.src.chi404_latency import resolve_latency_model


def _split_ms(total_ms: float, entry_ratio: float = 0.5) -> tuple[float, float]:
    entry = total_ms * entry_ratio
    resp = total_ms - entry
    if resp <= 0:
        resp = total_ms * 0.5
        entry = total_ms - resp
    return entry, resp


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=_REPO)
    parser.add_argument("--regime", action="append", default=["fast", "normal", "stress", "extreme"])
    args = parser.parse_args()
    repo = args.repo.resolve()
    summary_path = repo / "runtime" / "latency_reports" / "latency_summary.json"
    out_dir = repo / "reports" / "latency_baselines" / "live_r01_chicago"
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {}
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))

    new_send = summary.get("new_send_to_ack_ms") or {}
    ms = new_send.get("ms") if isinstance(new_send, dict) else {}
    if not isinstance(ms, dict):
        ms = {}
    p50 = float(ms.get("p50_ms") or summary.get("live_order_ack_p99_ms", 9.811) * 0.36)
    p90 = float(ms.get("p90_ms") or p50 * 1.2)
    p99 = float(ms.get("p99_ms") or summary.get("live_order_ack_p99_ms") or 9.811)
    p999 = float(ms.get("p99_9_ms") or ms.get("max_ms") or p99 * 1.4)

    regime_totals = {
        "fast": p50,
        "normal": p90,
        "stress": p99,
        "extreme": p999,
    }

    written: list[str] = []
    for regime in args.regime:
        total = regime_totals.get(regime, p99)
        entry, resp = _split_ms(total)
        try:
            model = resolve_latency_model(regime=regime, chi404_summary=summary_path)
        except (FileNotFoundError, ValueError):
            model = {
                "latency_model_family": "ConstantLatency",
                "latency_regime": regime,
                "feed_latency_source": "open_pending_cc2_campaign",
                "order_entry_latency_source": "derived_from_new_send_to_ack",
                "order_response_latency_source": "derived_from_new_send_to_ack",
                "latency_units": "milliseconds",
                "latency_source_authority": "hft3_native_cpp_rithmic_latency_probe",
                "latency_proxy_status": "measured_partial",
                "latency_component_mapping": {
                    "feed_latency": "feed_latency_us CC-2",
                    "order_entry_latency": "new_send_to_exchange_us CC-3",
                    "order_response_latency": "new_exchange_to_ack_us CC-3",
                },
                "feed_latency_ms": None,
                "order_entry_latency_ms": entry,
                "order_response_latency_ms": resp,
                "latency_p50_ms": p50,
                "latency_p90_ms": p90,
                "latency_p99_ms": p99,
                "order_response_latency_modeled": True,
                "native_latency_probe_host": "CHI404",
            }
        model["latency_regime"] = regime
        model["order_entry_latency_ms"] = entry
        model["order_response_latency_ms"] = resp
        model["latency_p99_ms"] = p99
        out_path = out_dir / f"latency_model_{regime}.json"
        out_path.write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
        written.append(str(out_path.relative_to(repo)).replace("\\", "/"))

    print(json.dumps({"written": written}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
