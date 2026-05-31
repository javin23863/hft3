"""Jump intensity and depth deterioration from mempool stress."""
from __future__ import annotations

import math


def jump_intensity_lambda(
    mempool_usage_bytes: float,
    fee_spike_zscore: float,
    *,
    alpha0: float = -6.0,
    alpha_bytes: float = 1e-9,
    alpha_z: float = 0.5,
) -> float:
    """λ_t = exp(α0 + α1 * bytes + α2 * z) Poisson intensity baseline."""
    log_lambda = alpha0 + alpha_bytes * mempool_usage_bytes + alpha_z * fee_spike_zscore
    return float(math.exp(log_lambda))


def forward_depth_deterioration(
    depth0: float,
    blockspace_stress_score: float,
    *,
    gamma: float = 0.1,
) -> float:
    """Depth_t = Depth_0 * exp(-γ * stress)."""
    return float(depth0 * math.exp(-gamma * blockspace_stress_score))
