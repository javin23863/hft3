#!/usr/bin/env python3
"""Audit prop-cohort feature slots for the all-zero defect documented in CORRECTNESS.md §3 prop-i.

This script reproduces the empirical proof that feature slots 31-34
(cutoff_pressure_score, prop_reentry_score, news_restriction_flatten_score,
max_contract_trade_imbalance) have no producer and are all-zero across the
MES feature store.  It serves two purposes:

  1. Pre-PC2 (now): confirm the defect is present.  Exit 1 if any prop slot is
     non-zero (would mean an undocumented producer exists and the audit is stale).

  2. Post-PC2 (after features/prop_features.py is built and the store rebuilt):
     this same exit-1 condition flips to a *success signal* -- the script exits 1
     precisely when the defect has been fixed, at which point this audit should be
     retired or repurposed as a non-degeneracy assertion.

Control slots (aggressor_volume_imbalance=0, book_slope=13, distance_to_vwap=30)
are checked as a sanity guard: if all three control slots are also zero the files
are likely wrong and the script exits 1 with a distinct message.

Expected pre-PC2 outcome (defect confirmed):
  Slots 31-34: 0.000% non-zero across all files sampled.
  Control slots: significantly non-zero (aggressor_volume_imbalance ~69%,
  book_slope ~99.5%, distance_to_vwap ~98.9%).
  Exit code: 0 (defect confirmed; no undocumented producer found).

Usage:
    python scripts/audit_prop_slots.py [--features-dir PATH] [--limit N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in (_REPO, _REPO / "packages"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import numpy as np

from features_engine.src.features.feature_index import FeatureIndex

from data_system.src.feature_store import feature_store_root  # noqa: E402

DEFAULT_FEATURES_DIR = feature_store_root(_REPO) / "MES.v.0"

PROP_SLOTS: list[tuple[int, str]] = [
    (int(FeatureIndex.CUTOFF_PRESSURE_SCORE),           "cutoff_pressure_score"),
    (int(FeatureIndex.PROP_REENTRY_SCORE),              "prop_reentry_score"),
    (int(FeatureIndex.NEWS_RESTRICTION_FLATTEN_SCORE),  "news_restriction_flatten_score"),
    (int(FeatureIndex.MAX_CONTRACT_TRADE_IMBALANCE),    "max_contract_trade_imbalance"),
]

CONTROL_SLOTS: list[tuple[int, str]] = [
    (int(FeatureIndex.AGGRESSOR_VOLUME_IMBALANCE), "aggressor_volume_imbalance"),
    (int(FeatureIndex.BOOK_SLOPE),                 "book_slope"),
    (int(FeatureIndex.DISTANCE_TO_VWAP),           "distance_to_vwap"),
]


def _load_X(path: Path) -> np.ndarray | None:
    """Load the 'X' feature matrix from a feature_v1 npz file.  Returns None on error."""
    try:
        with np.load(path) as npz:
            if "X" not in npz:
                return None
            return npz["X"]
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--features-dir",
        type=Path,
        default=DEFAULT_FEATURES_DIR,
        help="Directory containing MES feature_v1.npz files (default: %(default)s)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum number of files to load (default: %(default)s)",
    )
    args = parser.parse_args()

    features_dir: Path = args.features_dir
    if not features_dir.is_dir():
        print(f"ERROR: features-dir not found: {features_dir}", file=sys.stderr)
        return 1

    npz_files = sorted(features_dir.glob("*features_v1.npz"))[: args.limit]
    if not npz_files:
        print(f"ERROR: no *features_v1.npz files found in {features_dir}", file=sys.stderr)
        return 1

    all_slots = PROP_SLOTS + CONTROL_SLOTS
    total_rows: int = 0
    nonzero_counts: dict[int, int] = {slot: 0 for slot, _ in all_slots}
    files_loaded: int = 0
    files_skipped: int = 0

    for npz_path in npz_files:
        X = _load_X(npz_path)
        if X is None or X.ndim != 2:
            files_skipped += 1
            continue
        n_rows, n_cols = X.shape
        for slot, _ in all_slots:
            if slot < n_cols:
                nonzero_counts[slot] += int(np.count_nonzero(X[:, slot]))
        total_rows += n_rows
        files_loaded += 1

    print(f"\nProp-slot audit  |  files_loaded={files_loaded}  files_skipped={files_skipped}  total_rows={total_rows:,}")
    print(f"features_dir: {features_dir}")
    print()

    col_w = max(len(name) for _, name in all_slots)
    header = f"  {'slot':>4}  {'name':<{col_w}}  {'nonzero':>12}  {'pct_nonzero':>12}  type"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for slot, name in PROP_SLOTS:
        nz = nonzero_counts[slot]
        pct = 100.0 * nz / total_rows if total_rows > 0 else 0.0
        flag = "DEFECT (no producer)" if pct == 0.0 else "OK -- producer found (stale audit)"
        print(f"  {slot:>4}  {name:<{col_w}}  {nz:>12,}  {pct:>11.3f}%  {flag}")

    print()
    for slot, name in CONTROL_SLOTS:
        nz = nonzero_counts[slot]
        pct = 100.0 * nz / total_rows if total_rows > 0 else 0.0
        flag = "OK" if pct > 0.0 else "FAIL (sanity: wrong files?)"
        print(f"  {slot:>4}  {name:<{col_w}}  {nz:>12,}  {pct:>11.3f}%  {flag}")

    print()

    # Sanity check: if all control slots are zero the corpus is suspect.
    all_controls_zero = all(nonzero_counts[slot] == 0 for slot, _ in CONTROL_SLOTS)
    if all_controls_zero:
        print("FAIL: all control slots are zero -- check --features-dir; these may not be MES feature files.")
        return 1

    # Success signal check: exit 1 if any prop slot is non-zero (producer now exists; audit is stale post-PC2).
    any_prop_nonzero = any(nonzero_counts[slot] > 0 for slot, _ in PROP_SLOTS)
    if any_prop_nonzero:
        print("EXIT 1: one or more prop slots (31-34) are non-zero.")
        print("This means a producer now exists.  The CORRECTNESS.md prop-i defect may be resolved.")
        print("Verify PC2 completion and update/retire this audit accordingly.")
        return 1

    print("Defect confirmed: all prop slots (31-34) are 0.000% non-zero across this corpus.")
    print("Control slots are live.  This matches CORRECTNESS.md §3 prop-i.")
    print("Exit 0 -- defect present, no undocumented producer found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
