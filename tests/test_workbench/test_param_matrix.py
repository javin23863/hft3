"""Tests for parameter matrix generation."""

from __future__ import annotations


def test_generate_param_grid_hyp5_min_combos():
    from workbench.src.optimization.param_matrix import generate_param_grid

    grid = generate_param_grid("HYP_5", min_combinations=100)
    assert len(grid) >= 100
    assert all("parameter_hash" in g and "params" in g for g in grid)
    assert "signal_threshold" in grid[0]["params"]
