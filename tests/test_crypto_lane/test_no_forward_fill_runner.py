"""Runner must not forward-fill features (lookahead)."""
from __future__ import annotations

import inspect

from crypto_lane.src.ml import walk_forward_runner as wfr


def test_runner_does_not_forward_fill():
    src = inspect.getsource(wfr._evaluate_folds)
    assert "fill_null" not in src
    src2 = inspect.getsource(wfr._prepare_xy)
    assert "fill_null" not in src2
