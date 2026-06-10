"""Thin pytest wrapper for the C++ decision_runtime hardening binary.

The binary tests:
  (a) evaluate_actions writes all 10 ActionArray slots every call, with
      poisoned (0xCD) memory as the pre-condition (CORRECTNESS.md §2 row 8).
  (b) Non-promoted slots (ADD/REDUCE/FLATTEN/CANCEL/REPLACE/PASSIVE_JOIN/
      MARKETABLE_LIMIT) always receive NEG_INFINITY_SENTINEL
      (CHI404_RUNTIME.md §4.1-4.2).
  (c) get_optimal_action never returns a non-promoted code across 10 000
      randomised feature vectors (fixed seed).
  (d) Fallback: all-neg-inf ActionArray still returns NO_TRADE deterministically.
  (e) load_model rejects wrong magic, wrong version, feature_count==0,
      feature_count>64, and truncated files.

Compile target: build/test_decision_runtime_hardening.exe  (via CMake or
the standalone g++ invocation documented at the top of the .cpp source).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_BINARY_NAME = "test_decision_runtime_hardening.exe"
_BINARY = _REPO / "build" / _BINARY_NAME


def _try_build() -> bool:
    """Attempt to compile the binary with the project g++; return True on success."""
    gpp = (
        "C:/Users/MSI/AppData/Local/Microsoft/WinGet/Packages/"
        "BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe/"
        "mingw64/bin/g++.exe"
    )
    gpp_path = Path(gpp)
    if not gpp_path.exists():
        return False

    src_cpp = _REPO / "packages/decision_engine/cpp/src/decision_runtime.cpp"
    test_cpp = _REPO / "packages/decision_engine/cpp/tests/test_decision_runtime_hardening.cpp"
    include = _REPO / "packages/decision_engine/cpp/include"

    result = subprocess.run(
        [
            str(gpp_path),
            "-std=c++20", "-O2",
            f"-I{include}",
            str(src_cpp),
            str(test_cpp),
            "-o", str(_BINARY),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"[build stderr]\n{result.stderr}", file=sys.stderr)
    return result.returncode == 0


def _get_binary() -> Path:
    """Return the binary path, building it if missing."""
    if not _BINARY.exists():
        built = _try_build()
        if not built or not _BINARY.exists():
            pytest.skip(
                f"test_decision_runtime_hardening binary not found at {_BINARY} "
                "and auto-build failed. Run: cmake --build build (or the standalone "
                "g++ invocation in the .cpp header comment)."
            )
    return _BINARY


def test_decision_runtime_hardening_binary():
    """Run the C++ hardening binary and assert it exits 0 (all assertions passed)."""
    binary = _get_binary()
    result = subprocess.run(
        [str(binary)],
        cwd=str(_REPO / "build"),  # temp files written relative to cwd
        capture_output=True,
        text=True,
        timeout=60,
    )
    # Print stdout/stderr for CI log visibility.
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    assert result.returncode == 0, (
        f"C++ hardening binary exited {result.returncode}.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # Sanity: confirm the summary line is present.
    assert "ALL" in result.stdout and "PASSED" in result.stdout, (
        "Expected 'ALL ... tests PASSED' in binary output."
    )
