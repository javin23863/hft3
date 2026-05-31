"""HMM regime detection (4-state PDF model)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:
    from hmmlearn.hmm import GaussianHMM
except ImportError:  # pragma: no cover
    GaussianHMM = None


@dataclass
class RegimeState:
    state: str
    markup_prob: float
    state_probs: dict[str, float]


STATE_NAMES = [
    "accumulation",
    "markup",
    "distribution",
    "liquidation",
]


def infer_regime(mlofi_pc1: float, vpin: float, ofi_z: float) -> RegimeState:
    """Infer latent regime from emission vector [mlofi_pc1, vpin, ofi_z]."""
    x = np.array([[mlofi_pc1, vpin, ofi_z]], dtype=float)
    if GaussianHMM is not None and _has_trained_model():
        probs = _MODEL.predict_proba(x)[0]
        idx = int(np.argmax(probs))
        state = STATE_NAMES[idx] if idx < len(STATE_NAMES) else "unknown"
        markup_prob = float(probs[1]) if len(probs) > 1 else 0.0
        return RegimeState(
            state=state,
            markup_prob=markup_prob,
            state_probs={STATE_NAMES[i]: float(probs[i]) for i in range(min(len(probs), 4))},
        )
    return _heuristic_regime(mlofi_pc1, vpin, ofi_z)


def _heuristic_regime(mlofi_pc1: float, vpin: float, ofi_z: float) -> RegimeState:
    if vpin > 0.8 and ofi_z < -1.0:
        state = "liquidation"
        markup_prob = 0.05
    elif vpin > 0.6 and mlofi_pc1 < 0:
        state = "distribution"
        markup_prob = 0.15
    elif mlofi_pc1 > 0.5 and ofi_z > 0.5:
        state = "markup"
        markup_prob = 0.85
    else:
        state = "accumulation"
        markup_prob = 0.45
    return RegimeState(
        state=state,
        markup_prob=markup_prob,
        state_probs={state: 1.0},
    )


_MODEL: GaussianHMM | None = None


def _has_trained_model() -> bool:
    global _MODEL
    if _MODEL is not None:
        return True
    if GaussianHMM is None:
        return False
    rng = np.random.default_rng(42)
    samples = rng.normal(size=(200, 3))
    model = GaussianHMM(n_components=4, covariance_type="diag", n_iter=50, random_state=42)
    model.fit(samples)
    _MODEL = model
    return True
