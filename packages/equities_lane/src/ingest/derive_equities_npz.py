"""Derive quarantined NPZ from normalized equities NDJSON.

Mirror of CME ``derive_npz`` semantics but quarantined under ``data/equities/npz/``.
Never writes to ``data/npz/`` (production CME/NPZ is quarantined from equities per
AGENTS.md "Low-float equities lane (quarantined)").

Output NPZ contains the minimal columns needed for equities backtesting:
``ts_ns``, ``bid_px``, ``bid_sz``, ``ask_px``, ``ask_sz``, ``trade_px``,
``trade_sz``, ``aggressor``. Mirrors the ``SessionTick`` dataclass from
``equities_lane.src.models``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from equities_lane.src.ingest.session_io import load_session
from equities_lane.src.l3_policy import require_l3_session

_REPO = Path(__file__).resolve().parents[4]


def _npz_path(ndjson: Path) -> Path:
    """Map a normalized NDJSON file to its quarantined NPZ path.

    Trade-only files (e.g. ``AIRE_2025-07-10.ndjson``) map to
    ``data/equities/npz/<symbol>_<date>.npz``. Auction/imbalance siblings
    return None so the caller can skip them.
    """
    name = ndjson.stem
    if "imbalance" in name or "auction" in name:
        return None
    # AIRE_2025-07-10 -> AIRE, 2025-07-10
    parts = name.rsplit("_", 1)
    if len(parts) != 2:
        return None
    symbol, date = parts
    return _REPO / "data" / "equities" / "npz" / f"{symbol}_{date}.npz"


def derive_npz(ndjson_path: Path, npz_path: Path, *, force: bool = False) -> bool:
    """Convert one normalized NDJSON to a quarantined NPZ.

    Returns True if a new NPZ was written, False if skipped (already up to date).
    Raises if the session fails the L3 requirement.
    """
    if npz_path.exists() and not force:
        if npz_path.stat().st_mtime > ndjson_path.stat().st_mtime:
            return False
    meta, ticks = load_session(ndjson_path)
    require_l3_session(meta, l3_only=True, allow_degraded=False, context="derive_equities_npz")

    n = len(ticks)
    arr = {
        "ts_ns": np.fromiter((t.ts_ns for t in ticks), dtype=np.int64, count=n),
        "bid_px": np.fromiter((t.bid_px for t in ticks), dtype=np.float64, count=n),
        "bid_sz": np.fromiter((t.bid_sz for t in ticks), dtype=np.int32, count=n),
        "ask_px": np.fromiter((t.ask_px for t in ticks), dtype=np.float64, count=n),
        "ask_sz": np.fromiter((t.ask_sz for t in ticks), dtype=np.int32, count=n),
        "trade_px": np.fromiter(
            (np.nan if t.trade_px is None else t.trade_px for t in ticks),
            dtype=np.float64,
            count=n,
        ),
        "trade_sz": np.fromiter(
            (-1 if t.trade_sz is None else t.trade_sz for t in ticks),
            dtype=np.int32,
            count=n,
        ),
        "aggressor": np.fromiter(
            (0 if t.aggressor is None else (1 if t.aggressor == "buy" else 2) for t in ticks),
            dtype=np.int8,
            count=n,
        ),
    }
    npz_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(npz_path, **arr)
    # Sidecar JSON for inspection (gitignored, regenerable)
    sidecar = npz_path.with_suffix(".meta.json")
    sidecar.write_text(
        json.dumps(
            {
                "source": str(ndjson_path),
                "symbol": meta.symbol,
                "session_date": meta.session_date,
                "tick_count": n,
                "npz_path": str(npz_path),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="equities_lane.derive_equities_npz")
    parser.add_argument(
        "--normalized-root",
        default=str(_REPO / "data" / "equities" / "normalized"),
    )
    parser.add_argument(
        "--npz-root",
        default=str(_REPO / "data" / "equities" / "npz"),
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    src = Path(args.normalized_root)
    if not src.is_dir():
        print(f"normalized root not found: {src}", file=sys.stderr)
        return 1

    written = 0
    skipped = 0
    failures: list[tuple[str, str]] = []
    for ndjson in sorted(src.glob("*.ndjson")):
        target = _npz_path(ndjson)
        if target is None:
            continue
        try:
            if derive_npz(ndjson, target, force=args.force):
                written += 1
                print(f"wrote {target.name}")
            else:
                skipped += 1
                print(f"skipped (up to date) {target.name}")
        except Exception as exc:
            failures.append((ndjson.name, f"{type(exc).__name__}: {exc}"))
            print(f"FAILED {ndjson.name}: {exc}", file=sys.stderr)

    print(json.dumps({"written": written, "skipped": skipped, "failures": failures}, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
