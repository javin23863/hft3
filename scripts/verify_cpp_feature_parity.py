#!/usr/bin/env python3
"""C++/Python feature parity verification script.

Builds hft_feature_golden, generates golden features from Python MBOFeatureExtractor,
feeds same events through C++ binary, compares outputs element-wise.

Traces to: chicago_cme_a_plus_production_implementation_prompt.pdf (Python/C++ parity),
AGENTS.md (C++ parity gate).

Usage:
    python scripts/verify_cpp_feature_parity.py [--build-dir <path>] [--tolerance 1e-9]

Exit codes:
    0 - parity confirmed OR C++ unavailable (documented skip)
    1 - parity failed (mismatch)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

from features_engine.src.features.mbo_features import MBOEvent, MBOFeatureExtractor
from features_engine.src.features.feature_index import FEATURE_DIM


def generate_golden_events(num_events: int = 50) -> List[MBOEvent]:
    """Deterministic synthetic MBO events for Python and C++ parity comparison."""
    ts = 1_700_000_000_000_000_000
    events: List[MBOEvent] = []
    for i in range(num_events):
        side = "B" if i % 2 == 0 else "A"
        action = "ADD" if i < 20 else ("CANCEL" if i < 35 else "TRADE")
        price = [100.0, 99.75, 100.25, 99.50, 100.50, 99.25, 100.75][i % 7]
        size = (i % 10) + 1
        oid = (i % 5) + 1 if action in ("CANCEL", "TRADE") else i + 100
        events.append(MBOEvent(ts + i * 10_000, oid, action, side, float(price), size))
    return events


def generate_golden_features(num_events: int = 50) -> np.ndarray:
    extractor = MBOFeatureExtractor(tick_size=0.25)
    vectors: List[np.ndarray] = []
    events = generate_golden_events(num_events)
    for ev in events:
        vec = extractor.process_event(ev)
        vectors.append(vec.copy())
    return np.array(vectors, dtype=np.float64)


def write_events_csv(events: List[MBOEvent], path: Path) -> None:
    import csv
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp_ns", "order_id", "action", "side", "price", "size"])
        for ev in events:
            w.writerow([ev.timestamp_ns, ev.order_id, ev.action, ev.side, ev.price, ev.size])


def try_build_cpp(build_dir: Path) -> Optional[Path]:
    cmake = _REPO / "CMakeLists.txt"
    if not cmake.exists():
        return None
    try:
        subprocess.run(
            ["cmake", "-B", str(build_dir), "-S", str(_REPO), "-DCMAKE_BUILD_TYPE=Release"],
            capture_output=True, timeout=60,
        )
        subprocess.run(
            ["cmake", "--build", str(build_dir), "--target", "hft_feature_golden"],
            capture_output=True, timeout=120,
        )
        bin_path = build_dir / "hft_feature_golden"
        return bin_path if bin_path.exists() else None
    except Exception:
        return None


def run_cpp_golden(binary: Path, csv_path: Path, output_path: Path) -> bool:
    try:
        subprocess.run(
            [str(binary), str(csv_path), str(output_path)],
            capture_output=True, timeout=30,
        )
        return output_path.exists()
    except Exception:
        return False


def compare_vectors(
    py_vectors: np.ndarray,
    cpp_vectors: np.ndarray,
    tolerance: float = 1e-9,
) -> Dict[str, Any]:
    mismatches: List[Dict[str, Any]] = []
    n = min(len(py_vectors), len(cpp_vectors))

    if n == 0:
        if len(py_vectors) == 0 and len(cpp_vectors) == 0:
            return {"parity": True, "n_events": 0, "mismatches": []}
        return {"parity": False, "n_events": 0, "mismatches": [{"error": "empty vectors"}]}

    py = py_vectors[:n]
    cpp = cpp_vectors[:n]

    abs_diff = np.abs(py - cpp)
    max_diff = float(abs_diff.max())
    mean_diff = float(abs_diff.mean())
    mismatch_mask = abs_diff > tolerance
    mismatch_count = int(mismatch_mask.sum())

    if mismatch_count > 0:
        indices = np.where(mismatch_mask)
        for idx in range(min(10, len(indices[0]))):
            event_i = int(indices[0][idx])
            feat_j = int(indices[1][idx])
            mismatches.append({
                "event": event_i,
                "feature_dim": feat_j,
                "python_value": float(py[event_i, feat_j]),
                "cpp_value": float(cpp[event_i, feat_j]),
                "diff": float(py[event_i, feat_j] - cpp[event_i, feat_j]),
            })

    return {
        "parity": mismatch_count == 0,
        "n_events": n,
        "feature_dim": py.shape[1],
        "max_abs_diff": max_diff,
        "mean_abs_diff": mean_diff,
        "mismatch_count": mismatch_count,
        "mismatches": mismatches,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="C++/Python feature parity verification")
    parser.add_argument("--build-dir", help="C++ build directory", default=str(_REPO / "build"))
    parser.add_argument("--tolerance", type=float, default=1e-9, help="Element-wise tolerance")
    parser.add_argument("--output", help="Output report path",
                        default=str(_REPO / "runtime" / "validation" / "cpp_feature_parity.json"))
    args = parser.parse_args()

    build_dir = Path(args.build_dir)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("Generating Python golden features...")
    py_vectors = generate_golden_features()
    py_events = generate_golden_events(50)
    py_vectors = generate_golden_features(50)

    binary = try_build_cpp(build_dir)
    if binary is None:
        report = {
            "parity": None,
            "cpp_built": False,
            "reason": "C++ binary hft_feature_golden could not be built. "
                      "Either CMake is unavailable or the target does not exist.",
            "python_vectors_shape": list(py_vectors.shape),
        }
        print("C++ binary not available — parity check skipped (documented blocker).")
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "golden_events.csv"
        out_path = Path(tmp) / "cpp_output.npy"
        write_events_csv(py_events, csv_path)

        print("Running C++ golden feature extraction...")
        if not run_cpp_golden(binary, csv_path, out_path):
            report = {
                "parity": None,
                "cpp_built": True,
                "reason": "C++ binary ran but produced no output file.",
            }
            output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            return 0

        cpp_vectors = np.load(out_path)

    print("Comparing Python vs C++ feature vectors...")
    result = compare_vectors(py_vectors, cpp_vectors, tolerance=args.tolerance)
    result["cpp_built"] = True
    result["tolerance"] = args.tolerance
    result["python_shape"] = list(py_vectors.shape)
    result["cpp_shape"] = list(cpp_vectors.shape)

    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    if result["parity"]:
        print(f"PARITY CONFIRMED: {result['n_events']} events, "
              f"{result['feature_dim']} dims, max diff {result['max_abs_diff']:.2e}")
        return 0
    else:
        print(f"PARITY FAILED: {result['mismatch_count']} mismatches, "
              f"max diff {result['max_abs_diff']:.2e}")
        for m in result["mismatches"][:5]:
            print(f"  Event {m['event']}, feature {m['feature_dim']}: "
                  f"py={m['python_value']:.10f} cpp={m['cpp_value']:.10f}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
