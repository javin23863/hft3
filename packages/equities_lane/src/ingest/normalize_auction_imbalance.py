"""Normalize auction imbalance DBN/NDJSON to replayable NDJSON events."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


def iter_auction_records(path: Path) -> Iterator[dict[str, Any]]:
    if path.suffix == ".ndjson" or path.name.endswith(".ndjson"):
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)
        return
    try:
        import databento as db
    except ImportError:
        raise RuntimeError("databento required to read .dbn.zst auction files") from None
    store = db.DBNStore.from_file(path)
    for rec in store:
        yield _dbn_record_to_dict(rec)


def _dbn_record_to_dict(rec: Any) -> dict[str, Any]:
    d = {k: getattr(rec, k) for k in dir(rec) if not k.startswith("_") and isinstance(getattr(rec, k, None), (int, float, str))}
    ts = getattr(rec, "ts_event", None) or getattr(rec, "timestamp", 0)
    return {
        "ts_ns": int(ts),
        "auction_type": str(getattr(rec, "rtype", "auction")),
        "imbalance_side": str(getattr(rec, "side", "")),
        "paired_quantity": float(getattr(rec, "paired_qty", 0) or 0),
        "total_imbalance_quantity": float(getattr(rec, "imbalance_qty", 0) or 0),
        "indicative_price": float(getattr(rec, "price", 0) or 0),
        "reference_price": float(getattr(rec, "ref_price", 0) or 0),
        **d,
    }


def write_normalized_auction_ndjson(src: Path, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with dest.open("w", encoding="utf-8") as out:
        for rec in iter_auction_records(src):
            out.write(json.dumps(rec) + "\n")
            n += 1
    return n
