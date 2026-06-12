"""Post-replay feature snapshot derivation from normalized MBO events."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import numpy as np

from economic_event_universe.registry import default_snapshot_derivation_offsets


@dataclass(frozen=True)
class DerivedSnapshot:
    label: str
    offset_us: int | None
    kind: str | None
    timestamp_ns: int
    best_bid: str | None
    best_ask: str | None
    mid_price: str | None
    is_pre_release: bool


def _parse_offset_entry(entry: dict[str, Any]) -> tuple[str, int | None, str | None]:
    label = str(entry.get("label", ""))
    kind = entry.get("kind")
    if kind:
        return label, None, str(kind)
    offset_us = int(entry.get("offset_us", 0))
    return label, offset_us, None


def _mbo_event_from_normalized(ev: dict[str, Any]):
    from features_engine.src.features.mbo_features import MBOEvent

    action_map = {
        "add": "ADD",
        "cancel": "CANCEL",
        "modify": "MODIFY",
        "trade": "TRADE",
        "fill": "TRADE",
        "delete": "CANCEL",
    }
    act = action_map.get(str(ev.get("action", "")).lower(), "ADD")
    side = "B" if str(ev.get("side", "B")).upper().startswith("B") else "A"
    price = float(Decimal(str(ev.get("price", "0"))))
    return MBOEvent(
        timestamp_ns=int(ev.get("exchange_timestamp", 0)),
        order_id=int(ev.get("order_id", 0)),
        action=act,
        side=side,
        price=price,
        size=int(ev.get("size", 0)),
    )


def _state_from_extractor(extractor) -> tuple[str | None, str | None, str | None]:
    from features_engine.src.features.feature_index import FeatureIndex

    vec = extractor._vec  # noqa: SLF001
    mid = float(vec[FeatureIndex.MID_PRICE])
    spread = float(vec[FeatureIndex.SPREAD])
    if not np.isfinite(mid) or mid <= 0:
        return None, None, None
    half = spread / 2.0
    return (
        str(Decimal(str(mid - half))),
        str(Decimal(str(mid + half))),
        str(Decimal(str(mid))),
    )


def derive_snapshots_from_events(
    events: list[dict[str, Any]],
    *,
    release_timestamp_ns: int,
    offsets: tuple[dict[str, Any], ...] | None = None,
) -> list[DerivedSnapshot]:
    """Replay MBO events and capture snapshots at configured offsets."""
    from features_engine.src.features.mbo_features import MBOFeatureExtractor

    offs_cfg = offsets or default_snapshot_derivation_offsets()
    parsed_offsets = [_parse_offset_entry(o) for o in offs_cfg]

    target_times: list[tuple[str, int, str | None, bool]] = []
    for label, offset_us, kind in parsed_offsets:
        if kind == "release_boundary_pre":
            target_times.append((label, release_timestamp_ns - 1, kind, True))
        elif kind == "release_boundary_post":
            target_times.append((label, release_timestamp_ns, kind, False))
        elif offset_us is not None:
            target_times.append(
                (label, release_timestamp_ns + offset_us * 1000, None, offset_us < 0)
            )

    target_times.sort(key=lambda x: x[1])

    extractor = MBOFeatureExtractor(tick_size=0.25)
    timed_snaps: list[DerivedSnapshot] = []
    ti = 0

    ordered = sorted(
        events, key=lambda e: (int(e.get("sequence_number", 0)), int(e.get("exchange_timestamp", 0)))
    )

    def _capture(label: str, t_ns: int, kind: str | None, is_pre: bool) -> DerivedSnapshot:
        bid, ask, mid = _state_from_extractor(extractor)
        return DerivedSnapshot(
            label=label,
            offset_us=None if kind else (t_ns - release_timestamp_ns) // 1000,
            kind=kind,
            timestamp_ns=t_ns,
            best_bid=bid,
            best_ask=ask,
            mid_price=mid,
            is_pre_release=is_pre or t_ns < release_timestamp_ns,
        )

    for ev in ordered:
        ts = int(ev.get("exchange_timestamp", 0))
        extractor.process_event(_mbo_event_from_normalized(ev))

        while ti < len(target_times) and ts >= target_times[ti][1]:
            label, t_ns, kind, is_pre = target_times[ti]
            timed_snaps.append(_capture(label, t_ns, kind, is_pre))
            ti += 1

    while ti < len(target_times):
        label, t_ns, kind, is_pre = target_times[ti]
        timed_snaps.append(_capture(label, t_ns, kind, is_pre))
        ti += 1

    pre = next((s for s in timed_snaps if s.kind == "release_boundary_pre"), None)
    post = next((s for s in timed_snaps if s.kind == "release_boundary_post"), None)
    regular = [s for s in timed_snaps if s.kind not in ("release_boundary_pre", "release_boundary_post")]

    out: list[DerivedSnapshot] = []
    if pre is not None:
        out.append(pre)
    if post is not None:
        if pre is not None and pre.timestamp_ns == post.timestamp_ns and pre.mid_price == post.mid_price:
            post = DerivedSnapshot(
                label=post.label,
                offset_us=post.offset_us,
                kind=post.kind,
                timestamp_ns=post.timestamp_ns + 1,
                best_bid=post.best_bid,
                best_ask=post.best_ask,
                mid_price=post.mid_price,
                is_pre_release=False,
            )
        out.append(post)
    out.extend(regular)
    return out


def snapshots_to_dicts(snaps: list[DerivedSnapshot]) -> list[dict[str, Any]]:
    return [
        {
            "label": s.label,
            "offset_us": s.offset_us,
            "kind": s.kind,
            "timestamp_ns": s.timestamp_ns,
            "best_bid": s.best_bid,
            "best_ask": s.best_ask,
            "mid_price": s.mid_price,
            "is_pre_release": s.is_pre_release,
        }
        for s in snaps
    ]
