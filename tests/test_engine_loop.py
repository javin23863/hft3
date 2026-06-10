"""tests/test_engine_loop.py — pytest wrapper for test_engine_loop.cpp binary.

Mirrors the pattern from tests/test_decision_runtime_hardening.py.

Binary tested:
  build/test_engine_loop.exe

The binary covers CORRECTNESS.md row 7 gate proofs:
  - BLOCK → submit counter stays 0
  - HALT  → submit counter stays 0
  - FLATTEN reduce-only carve-out
  - Order-drain-before-decision ordering
  - Halted loop zero submissions
  - Normal run produces log records
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO        = Path(__file__).resolve().parents[1]
_BINARY_NAME = "test_engine_loop.exe"
_BINARY      = _REPO / "build" / _BINARY_NAME

_GPLUS = (
    "C:/Users/MSI/AppData/Local/Microsoft/WinGet/Packages/"
    "BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe/"
    "mingw64/bin/g++.exe"
)


def _try_build() -> bool:
    gpp = Path(_GPLUS)
    if not gpp.exists():
        return False

    includes = [
        f"-I{_REPO / 'rithmic_gateway' / 'include'}",
        f"-I{_REPO / 'risk_engine' / 'include'}",
        f"-I{_REPO / 'packages' / 'decision_engine' / 'cpp' / 'include'}",
        f"-I{_REPO / 'packages' / 'features_engine' / 'cpp' / 'include'}",
        f"-I{_REPO / 'engine' / 'include'}",
    ]
    sources = [
        str(_REPO / "risk_engine" / "src" / "risk_manager.cpp"),
        str(_REPO / "packages" / "decision_engine" / "cpp" / "src" / "decision_runtime.cpp"),
        str(_REPO / "packages" / "features_engine" / "cpp" / "src" / "feature_extractor.cpp"),
        str(_REPO / "packages" / "features_engine" / "cpp" / "src" / "event_context.cpp"),
        str(_REPO / "packages" / "features_engine" / "cpp" / "src" / "regime_filter.cpp"),
        str(_REPO / "engine" / "src" / "engine_config.cpp"),
        str(_REPO / "engine" / "tests" / "test_engine_loop.cpp"),
    ]
    out = str(_BINARY)
    _BINARY.parent.mkdir(exist_ok=True)
    result = subprocess.run(
        [str(gpp), "-std=c++20", "-O2", *includes, *sources, "-o", out],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"[build stderr]\n{result.stderr}", file=sys.stderr)
    return result.returncode == 0 and Path(out).exists()


def _get_binary() -> Path:
    if not _BINARY.exists():
        built = _try_build()
        if not built or not _BINARY.exists():
            pytest.skip(
                f"test_engine_loop binary not found at {_BINARY} and auto-build failed. "
                "Run cmake --build build or the standalone g++ in test_engine_loop.cpp header."
            )
    return _BINARY


def test_engine_loop_binary():
    """Run the C++ gate-proof binary; assert exit 0 (all assertions passed)."""
    binary = _get_binary()
    result = subprocess.run(
        [str(binary)],
        cwd=str(_REPO),  # run from repo root so events.csv relative path resolves
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    assert result.returncode == 0, (
        f"test_engine_loop binary exited {result.returncode}.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "ALL" in result.stdout and "PASSED" in result.stdout, (
        "Expected 'ALL ... tests PASSED' in binary output."
    )
