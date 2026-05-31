"""Jump intensity and depth deterioration."""
from __future__ import annotations

from crypto_lane.src.math.jump_intensity import forward_depth_deterioration, jump_intensity_lambda


def test_jump_intensity_increases_with_stress():
    low = jump_intensity_lambda(1e6, 0.0)
    high = jump_intensity_lambda(1e8, 3.0)
    assert high > low


def test_depth_deterioration_decreases_with_stress():
    d0 = 100.0
    assert forward_depth_deterioration(d0, 0.0) == d0
    assert forward_depth_deterioration(d0, 5.0) < d0
