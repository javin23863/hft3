"""K8 gate script — CRYPTO_LIVE.md §8 row K8.

Usage:
    python scripts/crypto_kill_drill.py --venue bitfinex_paper
    python scripts/crypto_kill_drill.py --simulated
    python scripts/crypto_kill_drill.py --venue bitfinex_paper --budget-s 0.5

Exit 0: all assertions hold.
Exit 1: assertion failure or real-venue error (reason printed to stderr).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time


# ---------------------------------------------------------------------------
# Simulated transport (no network; same shape as FakeTransport in tests)
# ---------------------------------------------------------------------------

class _SimTransport:
    def __init__(self) -> None:
        self.order_new_calls: list[dict] = []
        self.order_cancel_calls: list = []
        self.cancel_all_calls: int = 0
        self._open: list = []

    def order_new(self, body: dict):
        self.order_new_calls.append(body)
        cid = body.get("cid")
        return [0, "SUCCESS", None, None, [[None, None, cid, None, None]]]

    def order_cancel(self, body: dict):
        self.order_cancel_calls.append(body)
        return [0, "SUCCESS"]

    def cancel_all(self):
        self.cancel_all_calls += 1
        return [0, "SUCCESS"]

    def open_orders(self):
        return self._open


# ---------------------------------------------------------------------------
# Main drill logic
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="K8 kill-switch drill")
    # Live-venue drill (C12+) re-runs on Contabo host with its own explicit tooling.
    p.add_argument("--venue", default="bitfinex_paper", choices=["bitfinex_paper"])
    p.add_argument("--simulated", action="store_true", help="Use in-process simulated transport (no network)")
    p.add_argument("--budget-s", type=float, default=1.0, dest="budget_s", help="Cancel-all latency budget in seconds")
    return p.parse_args()


def _ensure_env() -> None:
    if not os.environ.get("EXECUTION_MODE"):
        os.environ["EXECUTION_MODE"] = "PAPER"
    if not os.environ.get("LIVE_KILL_SWITCH"):
        os.environ["LIVE_KILL_SWITCH"] = "armed"


def _auto_simulated() -> tuple[bool, bool]:
    """Return (force_sim, had_keys). True when API keys absent."""
    key = os.environ.get("HFT3_CRYPTO_BITFINEX_API_KEY", "")
    secret = os.environ.get("HFT3_CRYPTO_BITFINEX_API_SECRET", "")
    if not key or not secret:
        return True, False
    return False, True


def run_drill(venue: str, simulated: bool, budget_s: float) -> dict:
    _ensure_env()

    auto_sim, had_keys = _auto_simulated()
    if not simulated and auto_sim:
        print(
            "[drill] HFT3_CRYPTO_BITFINEX_API_KEY/SECRET absent — running simulated transport.\n"
            "        Real-venue drill (C8/C12) re-runs on the Contabo host with live keys.\n"
            "        Workstation gate evidence = simulated transport through REAL adapter/risk/kill code path.",
            file=sys.stderr,
        )
        simulated = True

    # Build transport
    sim_transport: _SimTransport | None = None
    if simulated:
        sim_transport = _SimTransport()
        transport_arg = sim_transport
    else:
        transport_arg = None  # BitfinexTransport built inside adapter with real keys

    # Import after env is set so safety.execution_mode() reads correctly
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages"))
    from execution.adapter_factory import create_adapter
    from execution.crypto_risk import CryptoKillSwitch, execute_kill_sequence
    from execution.interfaces import OrderEventType, OrderIntent, new_intent_id

    try:
        adapter = create_adapter(
            "PAPER",
            run_id="kill-drill",
            venue="crypto",
            transport=transport_arg,
        )
    except Exception as exc:
        print(f"[drill] adapter construction failed: {exc}", file=sys.stderr)
        sys.exit(1)

    # 1. Submit one resting order — must be ACCEPTED (risk gate armed path works)
    intent = OrderIntent(
        intent_id=new_intent_id(),
        run_id="kill-drill",
        timestamp_ns=time.time_ns(),
        strategy_id="drill",
        model_id="CRYPTO",
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        price=1.0,          # arbitrary low resting price
        quantity=0.0001,    # minimum viable qty
    )
    ev_pre = adapter.submit_order(intent)
    if ev_pre.event_type != OrderEventType.ORDER_ACCEPTED:
        print(
            f"[drill] pre-fire submit not ACCEPTED: {ev_pre.event_type} / {ev_pre.rejection_reason}",
            file=sys.stderr,
        )
        sys.exit(1)

    # 2. Execute kill sequence
    ks = CryptoKillSwitch()
    report = execute_kill_sequence(adapter, ks, clock=time.perf_counter)

    # 3. Post-fire: new submit must be REJECTED
    intent2 = OrderIntent(
        intent_id=new_intent_id(),
        run_id="kill-drill",
        timestamp_ns=time.time_ns(),
        strategy_id="drill",
        model_id="CRYPTO",
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        price=1.0,
        quantity=0.0001,
    )
    ev_post = adapter.submit_order(intent2)
    post_blocked = ev_post.event_type == OrderEventType.ORDER_REJECTED

    # 4. Gather transport metrics
    # Sim mode: report observed transport call count for transparency.
    # Real mode: cancel_ok from execute_kill_sequence (broker confirmed cancel-all).
    within_budget = report["cancel_all_elapsed_s"] <= budget_s

    # 5. Assertions — pass requires kill sequence pass AND post-fire blocked AND pre-fire accepted
    assertions_ok = report["pass"] and post_blocked and (ev_pre.event_type == OrderEventType.ORDER_ACCEPTED)

    result: dict = {
        "venue": venue,
        "simulated": simulated,
        "cancel_ok": report["cancel_ok"],
        "post_fire_submit_blocked": post_blocked,
        "cancel_all_elapsed_s": report["cancel_all_elapsed_s"],
        "within_budget": within_budget,
        "pass": assertions_ok,
    }
    if simulated and sim_transport is not None:
        result["cancel_all_calls"] = sim_transport.cancel_all_calls
    return result


def main() -> None:
    args = _parse_args()
    result = run_drill(args.venue, args.simulated, args.budget_s)
    print(json.dumps(result))
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
