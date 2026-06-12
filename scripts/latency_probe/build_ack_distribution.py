#!/usr/bin/env python3
"""Build the empirical order-ack latency distribution artifact.

Reads native-C++ Rithmic probe JSONL paired samples from
data/latency_baselines/<date>/*.jsonl, computes the full distribution,
and writes runtime/latency_reports/order_ack_distribution.json.

Reuses parsing helpers from native_probe_orders (_load_jsonl_dir,
_percentile) to stay consistent with the rest of the repo.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROBE_DIR = Path(__file__).resolve().parent
if str(_PROBE_DIR) not in sys.path:
    sys.path.insert(0, str(_PROBE_DIR))

# Reuse the canonical JSONL parser + percentile helper from native_probe_orders.
from native_probe_orders import _load_jsonl_dir, _percentile  # noqa: E402


def _log_spaced_edges(min_val: float, max_val: float, n_bins: int = 50) -> list[float]:
    """Return n_bins+1 log-spaced bin edges between min_val and max_val."""
    lo = math.log10(max(min_val, 1e-9))
    hi = math.log10(max(max_val, min_val + 1e-9))
    step = (hi - lo) / n_bins
    return [10 ** (lo + i * step) for i in range(n_bins + 1)]


def _histogram(sorted_vals: list[float], edges: list[float]) -> list[int]:
    """Bin sorted_vals into len(edges)-1 buckets defined by edges.

    Each sample s falls in bucket i where edges[i] <= s < edges[i+1].
    The last bucket is closed on the right: edges[-2] <= s <= edges[-1].
    """
    n_bins = len(edges) - 1
    counts = [0] * n_bins
    for v in sorted_vals:
        # Binary search for the correct bin.
        lo, hi = 0, n_bins - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if v < edges[mid + 1]:
                hi = mid
            else:
                lo = mid + 1
        counts[lo] += 1
    return counts


def _moments(vals: list[float]) -> dict[str, float]:
    """Compute mean, std, skew, kurtosis (excess / Fisher) from raw values."""
    n = len(vals)
    if n == 0:
        return {"mean": float("nan"), "std": float("nan"), "skew": float("nan"), "kurtosis": float("nan")}
    mean = sum(vals) / n
    if n == 1:
        return {"mean": mean, "std": float("nan"), "skew": float("nan"), "kurtosis": float("nan")}
    var = sum((v - mean) ** 2 for v in vals) / n
    std = math.sqrt(var)
    if std == 0.0:
        return {"mean": mean, "std": 0.0, "skew": float("nan"), "kurtosis": float("nan")}
    m3 = sum((v - mean) ** 3 for v in vals) / n
    m4 = sum((v - mean) ** 4 for v in vals) / n
    skew = m3 / (std ** 3)
    kurt = m4 / (std ** 4) - 3.0  # excess kurtosis
    return {"mean": mean, "std": std, "skew": skew, "kurtosis": kurt}


def build_distribution(repo_root: Path) -> dict:
    """Build the full distribution artifact from all JSONL files under
    data/latency_baselines/<date>/*.jsonl.
    """
    latencies, sample_files, run_ids, symbols, skipped = _load_jsonl_dir(repo_root)

    n = len(latencies)
    if n == 0:
        raise ValueError(
            f"No valid paired samples found under {repo_root / 'data' / 'latency_baselines'}. "
            f"Skipped lines: {skipped}"
        )

    sv = sorted(latencies)

    percentile_points = [1, 5, 10, 25, 50, 75, 90, 95, 99, 99.9]
    percentiles: dict[str, float] = {}
    for p in percentile_points:
        key = f"p{p}" if p != 99.9 else "p99.9"
        percentiles[key] = _percentile(sv, float(p))
    percentiles["min"] = sv[0]
    percentiles["max"] = sv[-1]

    edges = _log_spaced_edges(sv[0], sv[-1], n_bins=50)
    counts = _histogram(sv, edges)

    moments = _moments(latencies)

    symbol = symbols[0] if len(symbols) == 1 else symbols

    artifact: dict = {
        "run_ids": run_ids,
        "symbol": symbol,
        "n_pairs": n,
        "unit": "us",
        "percentiles": percentiles,
        "histogram": {
            "bin_edges_us": edges,
            "counts": counts,
        },
        "moments": moments,
        "samples_source_files": sample_files,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Rithmic paper-system ack; conservative injection source; "
            "not the live offensive ceiling"
        ),
    }
    return artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build empirical order-ack latency distribution artifact."
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repository root (default: two levels above this script)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Output JSON path. Default: <repo>/runtime/latency_reports/"
            "order_ack_distribution.json"
        ),
    )
    args = parser.parse_args(argv)

    repo_root = args.repo.resolve()
    out_path: Path = args.out or (
        repo_root / "runtime" / "latency_reports" / "order_ack_distribution.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    artifact = build_distribution(repo_root)

    out_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")

    p = artifact["percentiles"]
    m = artifact["moments"]
    print(
        f"n={artifact['n_pairs']}  "
        f"p50={p['p50']:.1f}us  p90={p['p90']:.1f}us  "
        f"p99={p['p99']:.1f}us  p99.9={p['p99.9']:.1f}us  "
        f"mean={m['mean']:.1f}us  skew={m['skew']:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
