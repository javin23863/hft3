"""Event study CAS and fee Z-score."""
from __future__ import annotations

from crypto_lane.src.math.event_study import cumulative_abnormal_spread, fee_spike_event, rolling_fee_zscore


def test_fee_spike_event_triggers():
    fees = [1.0] * 20 + [100.0]
    assert fee_spike_event(fees, window=20, threshold=3.0) == 1


def test_cas_sums_abnormal_spread():
    spreads = [2.0, 2.5, 3.0]
    expected = [1.0, 1.0, 1.0]
    cas = cumulative_abnormal_spread(spreads, expected, 0, 2)
    assert abs(cas - 4.5) < 1e-9


def test_rolling_zscore_finite():
    assert isinstance(rolling_fee_zscore([1, 2, 3, 4, 5], 3), float)
