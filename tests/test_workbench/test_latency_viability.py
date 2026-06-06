"""Break-even latency analysis with C++ injection sweep."""

from pathlib import Path

import pytest

from workbench.src.latency.viability import (
    analyze_latency_viability,
    find_break_even_us,
    sweep_injection_pnl,
)
from workbench.src.sim.cpp_latency_profile import CppLatencyProfile
from workbench.src.sim.latency_simulator import LatencyPolicy

REPO = Path(__file__).resolve().parents[2]
SUMMARY = REPO / "runtime" / "latency_reports" / "latency_summary.json"


def test_break_even_interpolation_us():
    pnl = {0: 100.0, 1000: 50.0, 5000: -10.0, 10000: -50.0}
    be = find_break_even_us(pnl)
    assert 1000 < be < 5000


def test_rejects_when_cpp_buffer_negative():
    profile = CppLatencyProfile.from_chi404_summary(SUMMARY)
    pnl = {0: -100.0, 10000: -200.0}
    v = analyze_latency_viability(-100.0, profile, "sub_10ms", pnl_by_injection_us=pnl)
    assert v.recommendation == "REJECT"
    assert not v.survives_cpp_execution_delay


def test_injection_sweep_has_required_points():
    profile = CppLatencyProfile.from_chi404_summary(SUMMARY)
    assert 0 in profile.injection_sweep_us
    assert 1_000_000 in profile.injection_sweep_us


def test_fixed_latency_policy_is_forbidden():
    with pytest.raises(ValueError, match="Fixed/default latency policy is not allowed"):
        LatencyPolicy.fixed(1.0)
