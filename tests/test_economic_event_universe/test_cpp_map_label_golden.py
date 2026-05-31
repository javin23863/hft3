"""Compiled C++ map_label parity (skips if binary not built)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from economic_event_universe.labels import row_to_event_context

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))


def _golden_exe() -> Path | None:
    try:
        from workbench.src.sim.cpp_binary import resolve_cpp_binary

        return resolve_cpp_binary(_REPO, "hft_event_context_golden")
    except Exception:
        return None


def test_cpp_map_label_golden():
    exe = _golden_exe()
    if exe is None:
        pytest.skip("hft_event_context_golden not built (cmake --build build)")
    proc = subprocess.run([str(exe)], cwd=str(_REPO), capture_output=True, text=True, check=True)
    cpp = json.loads(proc.stdout.strip())
    for key, label in cpp.items():
        et, wn = key.split("|", 1)
        assert row_to_event_context(et, wn) == label
