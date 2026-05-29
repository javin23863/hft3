from __future__ import annotations

from collections import Counter
from typing import Any


def validate_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []
    duplicates = 0
    out_of_order = 0
    bad_prices = 0
    bad_sizes = 0
    crossed = 0
    locked = 0
    malformed_orders = 0
    unsupported = 0
    tz_errors = 0
    monotonic_violations = 0

    seen_keys: set[str] = set()
    prev_local: int | None = None
    supported = {
        "trade",
        "quote",
        "depth",
        "order_submit",
        "order_ack",
        "fill",
        "cancel",
        "reject",
        "position",
        "account",
    }

    for i, ev in enumerate(events):
        et = ev.get("event_type")
        if et not in supported:
            unsupported += 1
            warnings.append(f"unsupported event_type={et} at index {i}")

        local_ts = ev.get("local_receive_timestamp_ns")
        if local_ts is None:
            errors.append(f"missing local_receive_timestamp_ns at index {i}")
        elif prev_local is not None and int(local_ts) < prev_local:
            monotonic_violations += 1
        if local_ts is not None:
            prev_local = int(local_ts)

        exch_ts = ev.get("exchange_timestamp_ns")
        if exch_ts is None:
            warnings.append(f"missing exchange timestamp at index {i}")
        elif local_ts is not None and int(exch_ts) > int(local_ts) + 60_000_000_000:
            tz_errors += 1

        key = "|".join(
            str(ev.get(k, ""))
            for k in ("event_type", "exchange_timestamp_ns", "order_id", "fill_id", "sequence", "price", "size")
        )
        if key in seen_keys:
            duplicates += 1
        seen_keys.add(key)

        price = ev.get("price")
        if price is not None:
            try:
                p = float(price)
                if p <= 0:
                    bad_prices += 1
            except (TypeError, ValueError):
                bad_prices += 1

        size = ev.get("size")
        if size is not None:
            try:
                s = int(size)
                if s < 0:
                    bad_sizes += 1
            except (TypeError, ValueError):
                bad_sizes += 1

        bid = ev.get("bid_price")
        ask = ev.get("ask_price")
        if bid is not None and ask is not None:
            try:
                b, a = float(bid), float(ask)
                if b > a:
                    crossed += 1
                elif b == a:
                    locked += 1
            except (TypeError, ValueError):
                pass

        if et in ("order_submit", "order_ack", "fill", "cancel", "reject") and not ev.get("order_id"):
            malformed_orders += 1

    type_counts = dict(Counter(str(ev.get("event_type")) for ev in events))
    status = "fail" if errors else ("warn" if warnings else "pass")
    return {
        "status": status,
        "event_count": len(events),
        "event_type_counts": type_counts,
        "errors": errors,
        "warnings": warnings,
        "checks": {
            "duplicates": duplicates,
            "out_of_order": out_of_order,
            "bad_prices": bad_prices,
            "bad_sizes": bad_sizes,
            "crossed_books": crossed,
            "locked_books": locked,
            "malformed_order_fill_events": malformed_orders,
            "unsupported_event_types": unsupported,
            "timezone_errors": tz_errors,
            "local_timestamp_monotonicity_violations": monotonic_violations,
        },
    }
