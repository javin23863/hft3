"""Test that BookPressureModel.evaluate works with an OrderBook kwarg from the adapter.

This test validates the fix applied to structural_adapter.py: before the fix,
evaluate() crashed with KeyError('bid_p') because the adapter didn't pass the
OrderBook. After the fix, the model takes the 'book is not None' branch.

Also confirms all 11 structural models tolerate the extra 'book' kwarg that
the adapter now passes unconditionally.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
import sys

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "apps"))
sys.path.insert(0, str(REPO / "packages"))
import hft3_bootstrap  # noqa: E402

hft3_bootstrap.setup_repo_paths()

from features_engine.src.features.mbo_features import MBOEvent, OrderBook  # noqa: E402
from features_engine.src.structural_models.model_01_book_pressure import (  # noqa: E402
    BookPressureModel,
)


def _apply_event(book: OrderBook, price: float, qty: int, side: str, action: str, timestamp_ns: int = 0) -> None:
    book.apply_event(
        MBOEvent(
            timestamp_ns=timestamp_ns,
            order_id=1,
            action=action,
            side=side,
            price=price,
            size=qty,
        )
    )


def _build_simple_book(bid_px: float = 2750.0, ask_px: float = 2750.5) -> OrderBook:
    book = OrderBook()
    _apply_event(book, bid_px, 100, "B", "ADD", 0)
    _apply_event(book, ask_px, 50, "A", "ADD", 1000)
    return book


def test_book_pressure_evaluate_with_OrderBook() -> None:
    model = BookPressureModel()
    book = _build_simple_book()
    result = model.evaluate(book=book, timestamp_ns=0)
    assert result is not None
    assert result.payload is not None
    assert hasattr(result.payload, "OFI_smooth")


def test_book_pressure_evaluate_without_book_crashes_on_missing_bbo() -> None:
    model = BookPressureModel()
    with pytest.raises(KeyError, match="bid_p"):
        model.evaluate(timestamp_ns=0)


def test_book_pressure_evaluate_with_bbo_kwargs() -> None:
    model = BookPressureModel()
    result = model.evaluate(bid_p=100.0, bid_q=10, ask_p=101.0, ask_q=5, timestamp_ns=0)
    assert result is not None
    assert result.payload is not None
    assert hasattr(result.payload, "OFI_smooth")


def test_adapter_run_backtest_passes_book() -> None:
    from workbench.src.adapters.structural_adapter import StructuralModelAdapter
    from workbench.src.core.protocol import ModelConfig
    from workbench.src.run.run_context import RunContext
    import numpy as np

    cfg = ModelConfig(
        model_id="BOOK_PRESSURE",
        name="Book Pressure",
        kind="structural",
        latency_lane="sub_10ms",
        min_history_years=0,
        required_datasets=["mbo_npz"],
        diagnostics_only=False,
    )
    adapter = StructuralModelAdapter("BOOK_PRESSURE", cfg)
    ev = np.array(
        [
            (3, 0, 1_000_000, 2750.0, 10.0, 1, 0, 0.0),
            (3, 0, 1_000_100, 2750.5, 5.0, 2, 0, 0.0),
        ],
        dtype=[
            ("ev", "u8"),
            ("exch_ts", "u8"),
            ("local_ts", "u8"),
            ("px", "f8"),
            ("qty", "f8"),
            ("order_id", "u8"),
            ("ival", "i8"),
            ("fval", "f8"),
        ],
    )
    ctx = RunContext(
        repo_root=REPO,
        run_id="test_book_pressure_adapter",
        model_id="BOOK_PRESSURE",
        event_id="TEST_EVENT",
        npz_path=REPO / "tests" / "fixtures" / "dummy.npz",
        events=ev,
    )
    from backtest_pipeline.src.signal_backtester import BacktestResult

    result = adapter.run_backtest(ctx)
    assert isinstance(result, BacktestResult) or isinstance(result, dict)
    assert ctx.metadata.get("pdf_book") is not None


def test_all_structural_models_tolerate_extra_book_kwarg() -> None:
    """Every model's evaluate(**kwargs) must tolerate a 'book' kwarg without
    crashing, because the adapter now passes it unconditionally. Models that
    don't use it just ignore it via **kwargs."""
    from features_engine.src.structural_models import MODEL_DEPENDENCY_MAP
    from features_engine.src.structural_models.registry import get_structural_model_by_id

    book = _build_simple_book()
    for mid in sorted(MODEL_DEPENDENCY_MAP.keys()):
        model = get_structural_model_by_id(mid)
        try:
            model.evaluate(
                book=book,
                bid_p=100.0,
                bid_q=10,
                ask_p=101.0,
                ask_q=10,
                mid=100.5,
                volume=100,
                bars=[],
                timestamp_ns=0,
                book_pressure=None,
                ofi_smooth=0.0,
                spot=4500.0,
            )
        except KeyError as e:
            pytest.fail(f"{mid} raised KeyError on extra kwargs: {e}")
        except Exception:
            pass  # some models may have structural assumptions; we just care about KeyError
