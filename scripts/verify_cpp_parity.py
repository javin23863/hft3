"""
Verify parity between the Python MBOFeatureExtractor and the C++ pybind11 module.

Usage
-----
    python -S scripts/verify_cpp_parity.py --npz <path> [--tick-size 0.25] [--window-ns 1000000000]

Hard-fails (exit 2) if the C++ module cannot be imported.

Note: runs with python -S (no site-packages hook) so hftbacktest constants are
inlined here rather than imported via hftbacktest.types, which triggers a C
extension that hangs under -S.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time
from pathlib import Path
from typing import List

import numpy as np

# ── repo root on sys.path so sibling packages resolve ────────────────────────
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages" / "features_engine" / "src"))
# Make sure build/ is on path so the .pyd can be found
sys.path.insert(0, str(_REPO / "build"))

# ── hftbacktest constants (inlined to avoid C-extension hang under python -S) ─
# Values from hftbacktest/types.py — do NOT change without cross-checking.
ADD_ORDER_EVENT     = 10
CANCEL_ORDER_EVENT  = 11
MODIFY_ORDER_EVENT  = 12
FILL_EVENT          = 13
TRADE_EVENT         = 2
BUY_EVENT           = 1 << 29   # 536870912
SELL_EVENT          = 1 << 28   # 268435456


def _event_type(ev: int):
    base = int(ev) & 0xFF
    if base == ADD_ORDER_EVENT:
        return "ADD"
    if base == CANCEL_ORDER_EVENT:
        return "CANCEL"
    if base == MODIFY_ORDER_EVENT:
        return "MODIFY"
    if base in (TRADE_EVENT, FILL_EVENT):
        return "TRADE"
    return None


def _event_side(ev: int) -> str:
    if int(ev) & BUY_EVENT:
        return "B"
    if int(ev) & SELL_EVENT:
        return "A"
    return "B"


# ── C++ module — hard-fail if not importable ─────────────────────────────────
from features._cpp_loader import load_cpp_features  # noqa: E402

cpp_mod = load_cpp_features()
if cpp_mod is None:
    print(
        "ERROR: hft3_features_cpp not importable. Build it first:\n"
        "  g++ -O2 -std=c++20 -shared -DMS_WIN64 -DWIN32 \\\n"
        "      -I packages/features_engine/cpp/include \\\n"
        "      -I <python-include> -I <pybind11-include> -I <numpy-include> \\\n"
        "      packages/features_engine/cpp/bindings/py_features.cpp \\\n"
        "      packages/features_engine/cpp/src/*.cpp \\\n"
        "      <python-lib> -o build/hft3_features_cpp.cp312-win_amd64.pyd",
        file=sys.stderr,
    )
    sys.exit(2)

from features.mbo_features import MBOEvent, MBOFeatureExtractor  # noqa: E402
from features.feature_index import FeatureIndex, FEATURE_DIM  # noqa: E402

# Slot-name lookup for display
_SLOT_NAMES = {int(fi): fi.name for fi in FeatureIndex}


def load_npz_events_raw(path: str) -> np.ndarray:
    """Load events from NPZ without using hftbacktest.types."""
    with np.load(path) as archive:
        if "data" not in archive:
            raise ValueError(f"NPZ {path} missing 'data' array")
        raw = archive["data"]
    if raw.dtype.names is None:
        raise ValueError(f"NPZ {path} data is not structured")
    required = {"ev", "local_ts", "px", "qty", "order_id"}
    missing = required - set(raw.dtype.names)
    if missing:
        raise ValueError(f"NPZ {path} missing fields: {sorted(missing)}")
    if len(raw) == 0:
        raise ValueError(f"NPZ {path} has zero events")
    return raw


def iter_events(raw: np.ndarray) -> List[MBOEvent]:
    """Convert raw structured array to MBOEvent list (inlined, no hftbacktest import)."""
    out = []
    for row in raw:
        action = _event_type(int(row["ev"]))
        if action is None:
            continue
        out.append(MBOEvent(
            timestamp_ns=int(row["local_ts"]),
            order_id=int(row["order_id"]),
            action=action,
            side=_event_side(int(row["ev"])),
            price=float(row["px"]),
            size=int(row["qty"]),
        ))
    return out


def _resolve_npz(npz_arg: str) -> Path:
    p = Path(npz_arg)
    if p.is_absolute() and p.exists():
        return p
    if p.exists():
        return p.resolve()
    root = os.environ.get("HFT3_NPZ_ROOT")
    if root:
        for candidate in (Path(root) / npz_arg, Path(root) / p.name):
            if candidate.exists():
                return candidate
    raise FileNotFoundError(
        f"NPZ not found: {npz_arg!r}  (HFT3_NPZ_ROOT={os.environ.get('HFT3_NPZ_ROOT')!r})"
    )


def _run_python(events: List[MBOEvent], tick_size: float, window_ns: int):
    fe = MBOFeatureExtractor(tick_size=tick_size, rolling_window_ns=window_ns)
    t0 = time.perf_counter()
    last_vec = None
    for ev in events:
        last_vec = fe.process_event(ev)
    elapsed = time.perf_counter() - t0
    return (last_vec if last_vec is not None else np.zeros(FEATURE_DIM)), elapsed


def _run_cpp(events: List[MBOEvent], tick_size: float, window_ns: int):
    fe = cpp_mod.FeatureExtractor(tick_size, window_ns)
    t0 = time.perf_counter()
    for ev in events:
        fe.process_event(
            ev.timestamp_ns, ev.order_id, ev.action, ev.side, ev.price, ev.size
        )
    elapsed = time.perf_counter() - t0
    return fe.extract(), elapsed


def _print_diff_table(py_vec: np.ndarray, cpp_vec: np.ndarray) -> None:
    diff = np.abs(py_vec.astype(np.float64) - cpp_vec.astype(np.float64))
    nonzero_slots = np.where(diff > 0)[0]

    print(f"\nPer-slot |py - cpp| diff  (nonzero: {len(nonzero_slots)} / {FEATURE_DIM} slots)")
    print(f"{'Slot':>4}  {'Name':<35}  {'|diff|':>18}  {'py_val':>18}  {'cpp_val':>18}")
    print("-" * 100)
    if len(nonzero_slots) == 0:
        print("  (all slots identical)")
    else:
        for s in nonzero_slots:
            name = _SLOT_NAMES.get(int(s), f"slot_{s}")
            print(f"{s:4d}  {name:<35}  {diff[s]:>18.6g}  {py_vec[s]:>18.10g}  {cpp_vec[s]:>18.10g}")
    print()
    print(f"Max abs diff: {diff.max():.6g}  (slot {diff.argmax()}  "
          f"name={_SLOT_NAMES.get(int(diff.argmax()), '?')})")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify Python vs C++ feature extractor parity."
    )
    parser.add_argument("--npz", required=True)
    parser.add_argument("--tick-size", type=float, default=0.25)
    parser.add_argument("--window-ns", type=int, default=1_000_000_000)
    args = parser.parse_args()

    npz_path = _resolve_npz(args.npz)
    print(f"NPZ: {npz_path}")
    print(f"tick_size={args.tick_size}  window_ns={args.window_ns:,}")

    raw = load_npz_events_raw(str(npz_path))
    events = iter_events(raw)
    n = len(events)
    print(f"Events loaded: {n:,}")

    print("\nRunning Python MBOFeatureExtractor...")
    py_vec, py_elapsed = _run_python(events, args.tick_size, args.window_ns)
    py_eps = n / py_elapsed if py_elapsed > 0 else float("inf")
    print(f"  {py_elapsed:.3f}s  ({py_eps:,.0f} ev/sec)")

    print("\nRunning C++ FeatureExtractor...")
    cpp_vec, cpp_elapsed = _run_cpp(events, args.tick_size, args.window_ns)
    cpp_eps = n / cpp_elapsed if cpp_elapsed > 0 else float("inf")
    print(f"  {cpp_elapsed:.3f}s  ({cpp_eps:,.0f} ev/sec)")

    speedup = py_elapsed / cpp_elapsed if cpp_elapsed > 0 else float("inf")
    print(f"\nSpeedup C++ vs Python: {speedup:.1f}x")

    _print_diff_table(py_vec, cpp_vec)


if __name__ == "__main__":
    main()
