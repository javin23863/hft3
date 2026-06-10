"""scripts/export_replay_feed.py — NPZ → flat binary feed exporter.

Reads an HftBacktest NPZ MBO event file and writes a flat binary feed
compatible with engine/include/replay_adapter.hpp FeedRecord layout.

On-disk record format (34 bytes, little-endian, no padding):
    int64_t  ts_ns     — local_ts field from NPZ
    uint64_t order_id  — order_id field from NPZ
    int8_t   action    — 'A','C','M','T' (mapped from hftbacktest ev field)
    int8_t   side      — 'B','A' (mapped from hftbacktest ev field)
    double   price     — px field from NPZ
    double   size      — qty field from NPZ (written as double)

Deterministic: same NPZ → byte-identical output (NumPy structured array
iteration is deterministic; struct.pack is deterministic).

Usage:
    python -S scripts/export_replay_feed.py \\
        --npz  <path/to/file.npz> \\
        --out  <path/to/output.bin>

Environment:
    HFT3_NPZ_ROOT — optional default search directory for NPZ files.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

# Adjust sys.path so this can be run from any cwd.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "packages", "features_engine", "src"))

import numpy as np

# HftBacktest event-type constants (from hftbacktest.types).
# We re-derive them here to avoid importing hftbacktest at export time
# (the exporter should be fast and dependency-minimal).
_ADD_ORDER    = 1
_CANCEL_ORDER = 2
_MODIFY_ORDER = 4
_TRADE        = 3  # TRADE_EVENT
_FILL         = 5  # FILL_EVENT
_BUY_FLAG     = 1 << 13
_SELL_FLAG    = 1 << 14

try:
    from hftbacktest.types import (
        ADD_ORDER_EVENT,
        CANCEL_ORDER_EVENT,
        MODIFY_ORDER_EVENT,
        TRADE_EVENT,
        FILL_EVENT,
        BUY_EVENT,
        SELL_EVENT,
    )
    _ADD_ORDER    = ADD_ORDER_EVENT
    _CANCEL_ORDER = CANCEL_ORDER_EVENT
    _MODIFY_ORDER = MODIFY_ORDER_EVENT
    _TRADE        = TRADE_EVENT
    _FILL         = FILL_EVENT
    _BUY_FLAG     = BUY_EVENT
    _SELL_FLAG    = SELL_EVENT
except ImportError:
    pass  # Use fallback constants defined above.


# Flat binary record: 8+8+1+1+8+8 = 34 bytes, little-endian.
_RECORD_FMT = "<qQbbdd"
_RECORD_SIZE = struct.calcsize(_RECORD_FMT)
assert _RECORD_SIZE == 34, f"Record size mismatch: {_RECORD_SIZE}"

_ACTION_MAP = {
    _ADD_ORDER:    ord('A'),
    _CANCEL_ORDER: ord('C'),
    _MODIFY_ORDER: ord('M'),
    _TRADE:        ord('T'),
    _FILL:         ord('T'),  # treat fills as trades for feed purposes
}


def _ev_action(ev: int) -> int | None:
    base = int(ev) & 0xFF
    return _ACTION_MAP.get(base, None)


def _ev_side(ev: int) -> int:
    if int(ev) & _BUY_FLAG:
        return ord('B')
    if int(ev) & _SELL_FLAG:
        return ord('A')
    return ord('B')  # default bid


def export_npz_to_feed(npz_path: str, out_path: str) -> int:
    """Convert NPZ to flat binary feed.  Returns record count written."""
    with np.load(npz_path) as archive:
        if "data" not in archive:
            raise ValueError(f"NPZ {npz_path} missing 'data' array")
        raw = archive["data"]

    if raw.dtype.names is None:
        raise ValueError(f"NPZ {npz_path} data is not a structured array")

    required = {"ev", "local_ts", "px", "qty", "order_id"}
    missing = required - set(raw.dtype.names)
    if missing:
        raise ValueError(f"NPZ {npz_path} missing fields: {sorted(missing)}")

    written = 0
    with open(out_path, "wb") as fh:
        for row in raw:
            ev_int = int(row["ev"])
            action = _ev_action(ev_int)
            if action is None:
                continue  # skip non-MBO events

            ts_ns    = int(row["local_ts"])
            order_id = int(row["order_id"])
            side     = _ev_side(ev_int)
            price    = float(row["px"])
            size     = float(row["qty"])

            packed = struct.pack(_RECORD_FMT,
                                 ts_ns,
                                 order_id,
                                 action,
                                 side,
                                 price,
                                 size)
            fh.write(packed)
            written += 1

    return written


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export HftBacktest NPZ MBO events to flat binary feed.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--npz", required=True, help="Input NPZ file path.")
    parser.add_argument("--out", required=True, help="Output flat binary feed path.")
    args = parser.parse_args()

    if not os.path.exists(args.npz):
        sys.exit(f"[export_replay_feed] NPZ not found: {args.npz}")

    n = export_npz_to_feed(args.npz, args.out)
    print(f"[export_replay_feed] Wrote {n} records ({n * _RECORD_SIZE} bytes) -> {args.out}")


if __name__ == "__main__":
    main()
