"""Guarded hftbacktest stub for hosts without the real package.

Opt-in per test module: call ``_ensure_hftbacktest_stub()`` before importing
modules that bind hftbacktest.types/order constants at import time. The
workstation venv has no hftbacktest (it lives on CHI404/Vast); when the real
package is importable this is a no-op, so CHI404/Vast runs use the real
constants. Deliberately NOT a conftest: a directory-wide stub would change
the failure mode of unrelated known-red-local tests. Tests that need
behavioral fakes still install their own via monkeypatch.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from types import ModuleType

import numpy as np


def _ensure_hftbacktest_stub() -> None:
    if "hftbacktest" in sys.modules:
        return
    if importlib.util.find_spec("hftbacktest") is not None:
        return
    fake_types = ModuleType("hftbacktest.types")
    for name, value in {
        "EXCH_EVENT": 1 << 8,
        "LOCAL_EVENT": 1 << 9,
        "DEPTH_EVENT": 1,
        "TRADE_EVENT": 2,
        "ADD_ORDER_EVENT": 10,
        "CANCEL_ORDER_EVENT": 11,
        "MODIFY_ORDER_EVENT": 12,
        "FILL_EVENT": 13,
        "BUY_EVENT": 1 << 11,
        "SELL_EVENT": 1 << 12,
    }.items():
        setattr(fake_types, name, value)
    fake_types.event_dtype = np.dtype(  # type: ignore[attr-defined]
        [
            ("ev", np.uint32),
            ("exch_ts", np.int64),
            ("local_ts", np.int64),
            ("px", np.float64),
            ("qty", np.float64),
            ("order_id", np.uint64),
            ("ival", np.int64),
            ("fval", np.float64),
        ]
    )
    fake_order = ModuleType("hftbacktest.order")
    for name, value in {
        "GTC": 0,
        "LIMIT": 0,
        "PARTIALLY_FILLED": 2,
        "FILLED": 3,
        "CANCELED": 4,
        "EXPIRED": 5,
        "REJECTED": 6,
    }.items():
        setattr(fake_order, name, value)
    fake_data = ModuleType("hftbacktest.data")
    fake_data.validate_event_order = lambda _events: None  # type: ignore[attr-defined]
    fake_pkg = ModuleType("hftbacktest")
    fake_pkg.__path__ = []  # type: ignore[attr-defined]
    fake_pkg.__spec__ = importlib.machinery.ModuleSpec("hftbacktest", None)  # type: ignore[attr-defined]
    fake_pkg.types = fake_types  # type: ignore[attr-defined]
    fake_pkg.order = fake_order  # type: ignore[attr-defined]
    fake_pkg.data = fake_data  # type: ignore[attr-defined]
    fake_pkg.BacktestAsset = object  # type: ignore[attr-defined]
    fake_pkg.HashMapMarketDepthBacktest = object  # type: ignore[attr-defined]
    sys.modules.setdefault("hftbacktest", fake_pkg)
    sys.modules.setdefault("hftbacktest.types", fake_types)
    sys.modules.setdefault("hftbacktest.order", fake_order)
    sys.modules.setdefault("hftbacktest.data", fake_data)
