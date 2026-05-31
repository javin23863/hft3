from __future__ import annotations

from typing import Any


def reconstruct_book(events: list[dict[str, Any]]) -> dict[str, Any]:
    best_bid: float | None = None
    best_ask: float | None = None
    depth_events = 0
    quote_events = 0
    trade_events = 0
    limitations: list[str] = []

    for ev in events:
        et = ev.get("event_type")
        if et == "depth":
            depth_events += 1
        elif et == "quote":
            quote_events += 1
            if ev.get("bid_price") is not None:
                best_bid = float(ev["bid_price"])
            if ev.get("ask_price") is not None:
                best_ask = float(ev["ask_price"])
        elif et == "trade":
            trade_events += 1

    if depth_events == 0:
        limitations.append("No depth events; book reconstruction uses BBO quotes only")
    if quote_events == 0 and trade_events == 0:
        limitations.append("No quotes or trades available for book reconstruction")

    spread = None
    if best_bid is not None and best_ask is not None:
        spread = best_ask - best_bid

    status = "pass" if quote_events or depth_events or trade_events else "fail"
    return {
        "status": status,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "depth_events": depth_events,
        "quote_events": quote_events,
        "trade_events": trade_events,
        "limitations": limitations,
    }
