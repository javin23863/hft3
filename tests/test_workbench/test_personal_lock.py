"""Personal sandbox lock — local only, excluded from promotion."""

from __future__ import annotations

from pathlib import Path

from workbench.src.data.event_catalog import list_campaign_events, list_personal_events
from workbench.src.data.personal_lock import is_locked, set_unlocked
from decision_engine.python.src.walk_forward import ValidationPeriod

REPO = Path(__file__).resolve().parents[2]


def test_locked_hides_personal_events():
    set_unlocked(REPO, False)
    assert is_locked(REPO)
    assert list_personal_events("HYP_5", "MES.v.0", REPO) == []


def test_unlocked_allows_personal_mode_only():
    set_unlocked(REPO, True)
    try:
        assert not is_locked(REPO)
        personal = list_personal_events("HYP_5", "MES.v.0", REPO)
        promo = list_campaign_events(
            "HYP_5",
            ValidationPeriod("Personal", 2026, 2026),
            "MES.v.0",
            REPO,
            mode="promotion",
        )
        for e in promo:
            assert "2026" not in e.release_date[:4] or e.release_date < "2026-03-01"
    finally:
        set_unlocked(REPO, False)
