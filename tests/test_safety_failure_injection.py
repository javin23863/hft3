"""Thin pytest wrapper for the C++ safety failure-injection binary.

The binary tests (CORRECTNESS.md §2 row 6 / CHI404_RUNTIME.md §5):

  Group A: each of the 6 RithmicAdapter flags set alone → SafetyPoller.poll()
           PollResult fields + RiskManager FailureState routing + atomics set
           (halted / flatten_active / hard_halt) per §5 failure-action table.

  Group B: adm_alert_severity boundaries:
             sev=0/1 → no action
             sev=2   → LATENCY_SPIKE (risk.halted=true, hard_halt=false;
                        PollResult.halted=false — header-vs-spec discrepancy)
             sev>=3  → hard halt (PollResult.halted=true, hard_halt=true)

  Group C: combined-flag ordering — hard-halt sticky (once set, lower-severity
           flags do not downgrade/clear; SafetyPoller.hard_halted_ is sticky).

  Group D: ack_reconcile()/ack_book_resync() clear protocol — adapter flag
           cleared only via ack, not via repeated poll().

  Group E: queue-drop counter deltas → PollResult.md_drops_delta /
           order_drops_delta (telemetry only; no halt/resync side-effect).

  Group F: RiskManager.check_order direct — position limit breach → BLOCK,
           rate limit breach → BLOCK, post-halt → HALT, flatten_active +
           reduce-only → PASS, LATENCY_SPIKE → HALT, daily-loss-limit → FLATTEN.

Header-vs-spec discrepancy:
  CHI404_RUNTIME.md §5 says adm_alert_severity==2 sets "halted=true (soft)"
  in PollResult, but safety_poller.hpp does NOT set result.halted=true for
  sev==2 (only sev>=3 does).  Tests assert the header behaviour (header is
  current truth per task specification).

Compile target: build/test_safety_failure_injection.exe  (via CMake or the
standalone g++ invocation documented at the top of the .cpp source).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO        = Path(__file__).resolve().parents[1]
_BINARY_NAME = "test_safety_failure_injection.exe"
_BINARY      = _REPO / "build" / _BINARY_NAME

_GPP = (
    "C:/Users/MSI/AppData/Local/Microsoft/WinGet/Packages/"
    "BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe/"
    "mingw64/bin/g++.exe"
)


def _try_build() -> bool:
    """Attempt to compile the binary with the project g++; return True on success."""
    gpp_path = Path(_GPP)
    if not gpp_path.exists():
        return False

    test_cpp    = _REPO / "rithmic_gateway/tests/test_safety_failure_injection.cpp"
    risk_src    = _REPO / "risk_engine/src/risk_manager.cpp"
    inc_gw      = _REPO / "rithmic_gateway/include"
    inc_risk    = _REPO / "risk_engine/include"

    build_dir = _REPO / "build"
    build_dir.mkdir(exist_ok=True)

    result = subprocess.run(
        [
            str(gpp_path),
            "-std=c++20", "-O2",
            f"-I{inc_gw}",
            f"-I{inc_risk}",
            str(risk_src),
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
                f"test_safety_failure_injection binary not found at {_BINARY} "
                "and auto-build failed.  Run: cmake --build build (or the "
                "standalone g++ invocation in the .cpp header comment)."
            )
    return _BINARY


def test_safety_failure_injection_binary():
    """Run the C++ failure-injection binary and assert it exits 0 (all assertions passed)."""
    binary = _get_binary()
    result = subprocess.run(
        [str(binary)],
        cwd=str(_REPO / "build"),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    assert result.returncode == 0, (
        f"C++ failure-injection binary exited {result.returncode}.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "ALL" in result.stdout and "PASSED" in result.stdout, (
        "Expected 'ALL ... tests PASSED' in binary output."
    )
