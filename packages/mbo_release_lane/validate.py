"""Validate normalized MBO event streams — fail closed on blockers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from mbo_release_lane.blockers import BlockerCode, BlockerReport


def _book_reconstruction_smoke(events: list[dict[str, Any]]) -> BlockerReport:
    """Minimal order-book smoke test using Decimal prices."""
    report = BlockerReport()
    book: dict[int, tuple[str, Decimal, int]] = {}
    try:
        for ev in events[: min(len(events), 5000)]:
            oid = int(ev.get("order_id", 0))
            action = str(ev.get("action", "")).lower()
            side = str(ev.get("side", "B"))
            try:
                price = Decimal(str(ev.get("price", "0")))
            except InvalidOperation:
                report.add(BlockerCode.INVALID_PRICE, "non-decimal price", order_id=oid)
                return report
            size = int(ev.get("size", 0))
            if size < 0:
                report.add(BlockerCode.NEGATIVE_SIZE, "negative size", order_id=oid)
                return report
            if price <= 0 and action in ("add", "modify", "trade", "fill"):
                if action != "cancel":
                    report.add(BlockerCode.INVALID_PRICE, "non-positive price", order_id=oid)
                    return report
            if action == "add":
                book[oid] = (side, price, size)
            elif action == "cancel":
                book.pop(oid, None)
            elif action == "modify":
                if oid in book:
                    _, _, old = book[oid]
                    book[oid] = (side, price, size if size else old)
            elif action in ("trade", "fill"):
                if oid in book:
                    _, p, old = book[oid]
                    rem = max(0, old - size)
                    if rem == 0:
                        book.pop(oid, None)
                    else:
                        book[oid] = (side, p, rem)
        bids = sorted((p for s, p, q in book.values() if s == "B" and q > 0), reverse=True)
        asks = sorted(p for s, p, q in book.values() if s == "A" and q > 0)
        if bids and asks and bids[0] >= asks[0]:
            report.add(
                BlockerCode.BOOK_RECONSTRUCTION_FAILURE,
                "crossed book after replay segment",
                best_bid=str(bids[0]),
                best_ask=str(asks[0]),
            )
    except Exception as exc:
        report.add(BlockerCode.BOOK_RECONSTRUCTION_FAILURE, str(exc))
    return report


def validate_event_stream(events: list[dict[str, Any]]) -> tuple[BlockerReport, dict[str, Any]]:
    report = BlockerReport()
    if not events:
        report.add(BlockerCode.EMPTY_EVENT_STREAM, "no MBO events")
        return report, _empty_validation()

    seen: set[tuple[int, int, int]] = set()
    duplicate_count = 0
    gap_count = 0
    prev_seq: int | None = None
    prev_ts: int | None = None
    out_of_order = False

    first_ts = int(events[0].get("exchange_timestamp", 0))
    last_ts = int(events[-1].get("exchange_timestamp", 0))
    first_seq = int(events[0].get("sequence_number", 0))
    last_seq = int(events[-1].get("sequence_number", 0))

    for i, ev in enumerate(events):
        oid = ev.get("order_id")
        if oid is None or str(oid) in ("", "0"):
            report.add(BlockerCode.MISSING_ORDER_ID, "event missing order_id", index=i)
            break
        if not ev.get("side"):
            report.add(BlockerCode.MISSING_SIDE, "event missing side", index=i)
            break
        if ev.get("price") is None:
            report.add(BlockerCode.MISSING_PRICE, "event missing price", index=i)
            break
        if ev.get("size") is None:
            report.add(BlockerCode.MISSING_SIZE, "event missing size", index=i)
            break
        ts = int(ev.get("exchange_timestamp", 0))
        if ts <= 0:
            report.add(BlockerCode.MISSING_TIMESTAMP, "event missing exchange timestamp", index=i)
            break
        seq = int(ev.get("sequence_number", 0))
        if seq <= 0:
            report.add(BlockerCode.MISSING_SEQUENCE, "event missing sequence_number", index=i)
            break

        key = (seq, oid, ts)
        if key in seen:
            duplicate_count += 1
        seen.add(key)

        if prev_seq is not None:
            if seq < prev_seq:
                out_of_order = True
            elif seq > prev_seq + 1:
                gap_count += 1
        prev_seq = seq
        if prev_ts is not None and ts < prev_ts:
            out_of_order = True
        prev_ts = ts

    if out_of_order and not report.blocked:
        report.add(BlockerCode.OUT_OF_ORDER_UNRESOLVED, "timestamps or sequences not monotonic")
    if gap_count > 0 and not report.blocked:
        report.add(BlockerCode.SEQUENCE_GAP, f"{gap_count} sequence gap(s) detected", gap_count=gap_count)

    if not report.blocked:
        smoke = _book_reconstruction_smoke(events)
        report.blockers.extend(smoke.blockers)

    validation = {
        "release_id": events[0].get("release_id"),
        "symbol": events[0].get("symbol"),
        "event_count": len(events),
        "first_timestamp": first_ts,
        "last_timestamp": last_ts,
        "first_sequence": first_seq,
        "last_sequence": last_seq,
        "has_order_id": True,
        "has_lifecycle_events": any(
            str(e.get("action", "")).lower() in ("add", "cancel", "modify", "trade", "fill")
            for e in events
        ),
        "deterministic_ordering": not out_of_order,
        "sequence_gaps": gap_count,
        "duplicate_events": duplicate_count,
        "replay_valid": not report.blocked,
        "point_in_time_safe": not report.blocked,
        "blocker_status": "blocked" if report.blocked else "valid",
        **report.to_dict(),
    }
    return report, validation


def _empty_validation() -> dict[str, Any]:
    return {
        "event_count": 0,
        "first_timestamp": None,
        "last_timestamp": None,
        "first_sequence": None,
        "last_sequence": None,
        "has_order_id": False,
        "has_lifecycle_events": False,
        "deterministic_ordering": False,
        "sequence_gaps": 0,
        "replay_valid": False,
        "point_in_time_safe": False,
        "blocker_status": "blocked",
        "blocker_count": 1,
        "blockers": [],
    }
