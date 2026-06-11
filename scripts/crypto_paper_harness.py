#!/usr/bin/env python3
"""CLI for the C8 crypto paper harness (CRYPTO_LIVE.md §5).

Run with --simulated for plumbing validation (acks are NOT authoritative).
Real >=100/>=1000 pairs require the Bitfinex paper sub-account on the Contabo host
(CRYPTO_LIVE.md §2/§5).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo root and package path
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_PACKAGES = _REPO_ROOT / "packages"
for _p in (str(_PACKAGES), str(_PACKAGES / "execution"), str(_PACKAGES / "backtest_pipeline" / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Simulated transport
# ---------------------------------------------------------------------------

class _SimTransport:
    """Minimal fake transport for plumbing validation.

    order_new returns a synthetic success response after a tiny deterministic
    delay so the adapter records a real perf_counter_ns pair (not synthetic).
    The summary is forcibly marked simulated=True and measured=False by the CLI
    regardless of sample count — simulated acks are never authoritative.
    """

    _ACK_DELAY_NS = 2_000_000  # 2 ms deterministic delay

    def order_new(self, body: dict):
        time.sleep(self._ACK_DELAY_NS / 1e9)
        cid = body.get("cid", 0)
        return [0, "SUCCESS", None, None, [[None, None, cid, None, None]]]

    def order_cancel(self, body: dict):
        return [0, "SUCCESS"]

    def cancel_all(self):
        return [0, "SUCCESS"]

    def open_orders(self):
        return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="C8 crypto paper harness — drive adapter, record latency samples."
    )
    parser.add_argument("--run-id", default="c8-paper", help="Run identifier")
    parser.add_argument("--n", type=int, default=100, help="Number of orders to submit")
    parser.add_argument(
        "--base-price", type=float, default=60000.0, help="Limit order price (USD)"
    )
    parser.add_argument("--qty", type=float, default=0.0001, help="Order quantity")
    parser.add_argument(
        "--simulated",
        action="store_true",
        help="Use simulated transport (plumbing only; acks NOT authoritative)",
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=None,
        help="Output path for latency_summary.json (default: runtime/crypto_latency/latency_summary.json)",
    )
    args = parser.parse_args(argv)

    # Determine if we are in simulated mode
    api_key = os.environ.get("HFT3_CRYPTO_BITFINEX_API_KEY", "")
    api_secret = os.environ.get("HFT3_CRYPTO_BITFINEX_API_SECRET", "")
    simulated = args.simulated or not (api_key and api_secret)

    if simulated:
        print(
            "[crypto_paper_harness] PLUMBING RUN: simulated acks are NOT authoritative. "
            "Real >=100/>=1000 pairs require the Bitfinex paper sub-account on the "
            "Contabo host (CRYPTO_LIVE.md §2/§5).",
            file=sys.stderr,
        )

    # Set required env guards if absent
    if not os.environ.get("EXECUTION_MODE"):
        os.environ["EXECUTION_MODE"] = "PAPER"
    if not os.environ.get("LIVE_KILL_SWITCH"):
        os.environ["LIVE_KILL_SWITCH"] = "armed"

    # Imports after path setup
    from backtest_pipeline.src.crypto_latency import default_crypto_summary_path
    from execution.adapter_factory import create_adapter
    from execution.crypto_paper_harness import (
        CryptoPaperHarness,
        build_latency_summary,
        write_latency_summary,
    )

    transport = _SimTransport() if simulated else None

    # risk_check=lambda i: True so paper harness batch runs without full risk wiring
    adapter = create_adapter(
        "PAPER",
        venue="crypto",
        transport=transport,
        run_id=args.run_id,
        risk_check=lambda i: True,
    )

    harness = CryptoPaperHarness(adapter, run_id=args.run_id)
    samples = harness.run_batch(args.n, base_price=args.base_price, qty=args.qty)

    summary_path = args.summary_path or default_crypto_summary_path(_REPO_ROOT)

    summary = build_latency_summary(samples, run_id=args.run_id)

    # Simulated runs: force measured=False BEFORE write — simulated acks never authoritative.
    if simulated:
        summary["measured"] = False
        summary["simulated"] = True
        summary["order_ack_p99_ms"] = 0.0
        summary["paper_order_latency"]["measured"] = False
        summary["paper_order_latency"]["authoritative"] = False

    write_latency_summary(summary, summary_path)

    result = {
        "run_id": args.run_id,
        "simulated": simulated,
        "total_samples": summary["total_samples"],
        "paired_count": summary["paper_order_latency"]["paired_count"],
        "first_batch_complete": summary["first_batch_complete"],
        "measured": summary["paper_order_latency"]["measured"],
        "order_ack_p99_ms": summary["order_ack_p99_ms"],
    }
    print(json.dumps(result))

    ok = summary["total_samples"] >= args.n and (
        simulated or summary["first_batch_complete"]
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
