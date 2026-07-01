"""Same strategy runs against replay/paper/live adapter mocks without mode branches."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from backtest_pipeline.src.hypothesis_replay_strategy import ToyAlwaysLongStrategy
from execution.adapter_factory import create_adapter
from execution.adapters.live_broker import LiveBrokerAdapter
from execution.adapters.paper_broker import PaperBrokerAdapter
from execution.interfaces import OrderIntent, new_intent_id
from replay.replay_session import ReplaySession, ReplaySessionConfig


@pytest.fixture(scope="module")
def minimal_npz() -> str:
    from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz

    path = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "replay_minimal_mbo.npz"
    if not path.is_file():
        build_minimal_mbo_npz(path)
    return str(path)


def test_toy_strategy_runs_all_adapter_modes(minimal_npz: str, tmp_path: Path) -> None:
    os.environ["EXECUTION_MODE"] = "REPLAY"
    cfg = ReplaySessionConfig(
        npz_path=minimal_npz,
        max_steps=300,
        audit_dir=tmp_path / "replay",
    )
    replay_result = ReplaySession(cfg, ToyAlwaysLongStrategy()).run()
    assert replay_result["order_intent_count"] > 0

    os.environ["EXECUTION_MODE"] = "PAPER"
    paper = create_adapter("PAPER", run_id="test-paper")
    assert isinstance(paper, PaperBrokerAdapter)
    intent = OrderIntent(
        intent_id=new_intent_id(),
        run_id="test",
        timestamp_ns=1,
        strategy_id="toy",
        model_id="TOY",
        symbol="MES",
        side="BUY",
        order_type="LIMIT",
        price=5000.0,
        quantity=1.0,
    )
    ev = paper.submit_order(intent)
    assert ev.event_type.value == "ORDER_ACCEPTED"

    os.environ["LIVE_MAX_ORDER_SIZE"] = "1"
    os.environ["LIVE_DAILY_LOSS_LIMIT"] = "1000"
    os.environ["LIVE_KILL_SWITCH"] = "armed"
    os.environ["LIVE_RISK_ENABLED"] = "1"
    os.environ["EXECUTION_MODE"] = "LIVE"
    live = create_adapter("LIVE", run_id="test-live")
    assert isinstance(live, LiveBrokerAdapter)
    live.submit_order(intent)

    source = Path(__file__).resolve().parents[1] / "packages" / "backtest_pipeline" / "src" / "hypothesis_replay_strategy.py"
    text = source.read_text(encoding="utf-8").lower()
    assert 'if mode == "replay"' not in text
    assert 'if mode == "paper"' not in text
    assert 'if mode == "live"' not in text


def test_no_direct_hbt_submit_outside_adapter_boundary() -> None:
    """Grep-gate: hftbacktest submit_* calls stay behind the adapter boundary.

    The OrderIntent adapter (HftBacktestSimulatedExchangeAdapter) and the
    HBT-only lane's minimal probe are the only sanctioned direct callers.
    Any other `.submit_buy_order(` / `.submit_sell_order(` call site is a
    parity regression: order flow outside the audited lifecycle path.
    """
    repo = Path(__file__).resolve().parents[1]
    allowed = {
        # The OrderIntent adapter itself.
        repo / "packages" / "execution" / "adapters" / "hftbacktest_simulated_exchange.py",
        # HBT-only lane minimal probe (single sanctioned smoke order).
        repo / "packages" / "backtest_pipeline" / "src" / "hftbacktest_only_pipeline.py",
        # Legacy Stage-3 HBT4 minimal official replay (probe-class, same as above).
        repo / "packages" / "backtest_pipeline" / "src" / "hftbacktest_realism.py",
        # KNOWN DEBT: hybrid quote engine still drives hbt directly; its lane
        # is fail-closed in hbt_strategy_factory until migrated to the adapter.
        # Do not add new entries here without migrating them instead.
        repo / "packages" / "backtest_pipeline" / "src" / "pdf_hybrid_strategy.py",
    }
    offenders: list[str] = []
    for root in ("packages", "scripts", "apps"):
        for path in (repo / root).rglob("*.py"):
            if path in allowed or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if ".submit_buy_order(" in text or ".submit_sell_order(" in text:
                offenders.append(str(path.relative_to(repo)))
    assert offenders == [], f"direct hbt submit calls outside adapter boundary: {offenders}"


def test_removed_direct_submit_strategy_raises_import_error() -> None:
    import backtest_pipeline.src.hft_strategy as hft_strategy

    with pytest.raises(ImportError, match="CombinedHypothesisStrategy was removed"):
        hft_strategy.CombinedHypothesisStrategy  # noqa: B018
    # Canonical replacement stays importable from the shim.
    assert hft_strategy.CombinedHypothesisReplayStrategy is not None
