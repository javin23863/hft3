"""FOMC overlap uses context_priority (lower wins)."""

from economic_event_universe.registry import context_priority


def test_fomc_press_priority_wins_over_statement():
    assert context_priority("FOMC_PRESS") < context_priority("FOMC_STATEMENT")
    assert context_priority("FOMC_STATEMENT") < context_priority("FOMC_MINUTES")
