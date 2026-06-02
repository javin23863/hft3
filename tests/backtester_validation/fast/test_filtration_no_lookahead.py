"""T0: filtration / no-lookahead (options parity engine)."""
from __future__ import annotations

from options_lane.src.ingest.quote_aligner import align_quotes
from options_lane.src.models import LegQuote, ParityGroup
from options_lane.src.parity_engine import compute_violation, discount_factor


def _futures_group() -> ParityGroup:
    return ParityGroup.from_dict(
        {
            "id": "test_fut",
            "type": "futures_options",
            "rate": {"source": "constant", "value": 0.05},
            "legs": [
                {"role": "future", "symbol": "UL.c.0"},
                {"role": "call", "symbol": "UL.c.0", "strike": 5500, "right": "C"},
                {"role": "put", "symbol": "UL.c.0", "strike": 5500, "right": "P"},
            ],
            "threshold_ticks": 1.0,
            "tick_size": 0.25,
            "time_to_expiry_years": 0.25,
        }
    )


def _fair_quotes() -> dict[str, LegQuote]:
    future_mid = 5500.0
    strike = 5500.0
    rate = 0.05
    t_years = 0.25
    df = discount_factor(rate, t_years)
    theo = future_mid - df * strike
    put_mid = 10.0
    call_mid = theo + put_mid
    ts = 1_000_000_000
    return {
        "future": LegQuote("future", "UL.c.0", future_mid, future_mid, ts),
        "call": LegQuote("call", "UL.c.0", call_mid, call_mid, ts, strike=strike, right="C"),
        "put": LegQuote("put", "UL.c.0", put_mid, put_mid, ts, strike=strike, right="P"),
    }


def test_filtration_quote_at_t_plus_one_not_visible() -> None:
    group = _futures_group()
    fair = list(_fair_quotes().values())
    late_future = LegQuote("future", "UL.c.0", 99999.0, 99999.0, 2_000_000_000)
    snapshots = align_quotes(group, fair + [late_future])
    v_at_1s = compute_violation(group, snapshots[0])
    assert v_at_1s is not None
    assert abs(v_at_1s.residual) < 1e-9
    snap_at_2s = snapshots[-1]
    assert snap_at_2s.quotes["call"].timestamp_ns == 1_000_000_000
    assert snap_at_2s.quotes["future"].timestamp_ns == 2_000_000_000
    v_filtrated = compute_violation(group, snap_at_2s, as_of_ns=1_000_000_000)
    assert v_filtrated is None
    v_at_2s = compute_violation(group, snap_at_2s)
    assert v_at_2s is not None
    assert v_at_2s.edge_ticks >= 2.0
