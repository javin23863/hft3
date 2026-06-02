"""Load auction imbalance events for event-window alignment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator, List, Optional

from features_engine.src.imbalance.auction import AuctionImbalanceEvent, is_auction_window_phase

AUCTION_WINDOW_BY_EVENT_TYPE = {
    "CPI": "during_event",
    "NFP": "during_event",
    "OPEN": "open",
    "CLOSE": "close",
}


def iter_auction_ndjson(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def resolve_auction_path(repo: Path, event_id: str, symbol: str) -> Optional[Path]:
    candidates = [
        repo / "data" / "equities" / "raw" / f"{symbol}_{event_id}_imbalance.ndjson",
        repo / "data" / "equities" / "normalized" / f"{symbol}_{event_id}_auction.ndjson",
        repo / "tests" / "fixtures" / "imbalance_auction_sample.ndjson",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def load_auction_events(
    repo: Path,
    event_id: str,
    symbol: str,
) -> List[AuctionImbalanceEvent]:
    path = resolve_auction_path(repo, event_id, symbol)
    if path is None:
        return []
    return [AuctionImbalanceEvent.from_record(r) for r in iter_auction_ndjson(path)]


def window_phase_for_event(event_id: str, auction_type: str) -> str:
    prefix = event_id.split("_")[0].upper()
    if auction_type.lower() in ("open", "opening"):
        return "open"
    if auction_type.lower() in ("close", "closing"):
        return "close"
    if prefix in AUCTION_WINDOW_BY_EVENT_TYPE:
        mapped = AUCTION_WINDOW_BY_EVENT_TYPE[prefix]
        if is_auction_window_phase(mapped) or mapped == "during_event":
            return "auction_pub" if prefix in ("CPI", "NFP") else mapped
    return "auction_pub"
