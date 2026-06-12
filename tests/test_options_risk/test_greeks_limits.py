"""Tests for options_risk.src.greeks_limits.

Coverage
--------
- taper_factor: boundary values (12:00 → 1.0, 13:00 → 0.5, 14:00 → 0.0,
                15:00 → 0.0, 09:00 → 1.0).
- check_limits: each breach kind triggered individually; clean book passes.
- check_limits: margin soft and hard breaches.
- check_limits: 0DTE taper applied to naked short gamma.
- hedge_presence_invariant: naked short flagged, hedged short passes,
                            long-only passes, empty book passes.
- LimitsConfig.load: round-trip from configs/risk/options_limits.yaml.
- LimitsConfig.load: malformed config rejected fail-closed (missing key
                     raises KeyError, not a default).

All tests are deterministic and have no external I/O beyond the one
round-trip config load from the repo's own yaml file.
"""
from __future__ import annotations

import datetime
import sys
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Path setup — resolve repo root from this file's location
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[2]  # tests/ -> repo root
_PACKAGES = _REPO / "packages"
for _p in [str(_REPO), str(_PACKAGES)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from options_risk.src.greeks_limits import (
    BucketLimits,
    BookSnapshot,
    Breach,
    LimitsConfig,
    OptionPosition,
    PortfolioGreeks,
    TaperConfig,
    check_limits,
    hedge_presence_invariant,
    taper_factor,
    taper_factor_from_config,
)


# ---------------------------------------------------------------------------
# Helpers — build a minimal LimitsConfig in-memory
# ---------------------------------------------------------------------------

def _make_bucket(
    max_abs_delta: float = 10.0,
    max_abs_gamma: float = 4.0,
    max_short_vega_usd: float = 1000.0,
    max_abs_theta_usd: float = 2000.0,
) -> BucketLimits:
    return BucketLimits(
        max_abs_delta=max_abs_delta,
        max_abs_gamma=max_abs_gamma,
        max_short_vega_usd=max_short_vega_usd,
        max_abs_theta_usd=max_abs_theta_usd,
    )


def _make_config(
    bucket_name: str = "dte_0",
    bucket: BucketLimits | None = None,
    margin_soft: float = 0.50,
    margin_hard: float = 0.65,
    hedge_required: bool = True,
    taper_start: datetime.time = datetime.time(12, 0),
    taper_zero: datetime.time = datetime.time(14, 0),
) -> LimitsConfig:
    if bucket is None:
        bucket = _make_bucket()
    return LimitsConfig(
        buckets={bucket_name: bucket},
        margin_utilization_soft=margin_soft,
        margin_utilization_hard=margin_hard,
        hedge_presence_required=hedge_required,
        taper=TaperConfig(
            start_ct=taper_start,
            zero_by_ct=taper_zero,
            applies_to="naked_short_gamma",
            bucket=bucket_name,
        ),
    )


def _make_clean_greeks() -> PortfolioGreeks:
    """Greeks well within default limits."""
    return PortfolioGreeks(
        delta=1.0,
        gamma_1pct=0.5,   # positive = long gamma; no taper
        vega_usd=100.0,   # positive = long vega; no short-vega check
        theta_usd=-50.0,  # |50| < 2000 limit
    )


# ---------------------------------------------------------------------------
# taper_factor tests
# ---------------------------------------------------------------------------

class TestTaperFactor:
    """Pure function tests using canonical 12:00–14:00 CT window."""

    def test_before_start_returns_one(self):
        assert taper_factor(datetime.time(9, 0)) == 1.0

    def test_at_start_returns_one(self):
        assert taper_factor(datetime.time(12, 0)) == 1.0

    def test_midpoint_returns_half(self):
        result = taper_factor(datetime.time(13, 0))
        assert abs(result - 0.5) < 1e-12

    def test_at_zero_by_returns_zero(self):
        assert taper_factor(datetime.time(14, 0)) == 0.0

    def test_after_zero_by_returns_zero(self):
        assert taper_factor(datetime.time(15, 0)) == 0.0

    def test_slightly_past_start(self):
        # 12:30 → 30 minutes into 120-minute window → factor = 1 - 30/120 = 0.75
        result = taper_factor(datetime.time(12, 30))
        assert abs(result - 0.75) < 1e-12

    def test_three_quarters_through(self):
        # 13:30 → 90 minutes into 120-minute window → factor = 1 - 90/120 = 0.25
        result = taper_factor(datetime.time(13, 30))
        assert abs(result - 0.25) < 1e-12


class TestTaperFactorFromConfig:
    """taper_factor_from_config with a custom window."""

    def test_custom_window_at_start(self):
        start = datetime.time(10, 0)
        zero = datetime.time(11, 0)
        assert taper_factor_from_config(datetime.time(10, 0), start, zero) == 1.0

    def test_custom_window_at_zero(self):
        start = datetime.time(10, 0)
        zero = datetime.time(11, 0)
        assert taper_factor_from_config(datetime.time(11, 0), start, zero) == 0.0

    def test_custom_window_midpoint(self):
        start = datetime.time(10, 0)
        zero = datetime.time(12, 0)
        result = taper_factor_from_config(datetime.time(11, 0), start, zero)
        assert abs(result - 0.5) < 1e-12


# ---------------------------------------------------------------------------
# check_limits — clean book
# ---------------------------------------------------------------------------

class TestCheckLimitsCleanBook:
    def test_clean_book_no_breaches(self):
        config = _make_config()
        greeks = {"dte_0": _make_clean_greeks()}
        result = check_limits(
            greeks, config,
            now_ct_time=datetime.time(10, 0),
            margin_utilization=0.30,
        )
        assert result == []

    def test_unknown_bucket_in_greeks_ignored(self):
        config = _make_config(bucket_name="dte_0")
        greeks = {
            "dte_0": _make_clean_greeks(),
            "unknown_bucket": PortfolioGreeks(delta=999.0, gamma_1pct=0, vega_usd=0, theta_usd=0),
        }
        result = check_limits(
            greeks, config,
            now_ct_time=datetime.time(10, 0),
            margin_utilization=0.30,
        )
        assert result == []

    def test_empty_greeks_no_breaches(self):
        config = _make_config()
        result = check_limits(
            {}, config,
            now_ct_time=datetime.time(10, 0),
            margin_utilization=0.30,
        )
        assert result == []


# ---------------------------------------------------------------------------
# check_limits — delta breach
# ---------------------------------------------------------------------------

class TestCheckLimitsDelta:
    def test_delta_long_breach(self):
        config = _make_config(bucket_name="dte_0")
        greeks = {"dte_0": PortfolioGreeks(
            delta=15.0,   # > limit 10.0
            gamma_1pct=0.0,
            vega_usd=0.0,
            theta_usd=0.0,
        )}
        result = check_limits(greeks, config, now_ct_time=datetime.time(10, 0), margin_utilization=0.0)
        kinds = [b.kind for b in result]
        assert "delta" in kinds
        delta_breach = next(b for b in result if b.kind == "delta")
        assert delta_breach.bucket == "dte_0"
        assert delta_breach.value == 15.0
        assert delta_breach.limit == 10.0
        assert delta_breach.severity == "hard"

    def test_delta_short_breach(self):
        config = _make_config()
        greeks = {"dte_0": PortfolioGreeks(
            delta=-11.0,  # abs 11 > limit 10
            gamma_1pct=0.0,
            vega_usd=0.0,
            theta_usd=0.0,
        )}
        result = check_limits(greeks, config, now_ct_time=datetime.time(10, 0), margin_utilization=0.0)
        assert any(b.kind == "delta" for b in result)

    def test_delta_at_limit_no_breach(self):
        config = _make_config()
        greeks = {"dte_0": PortfolioGreeks(delta=10.0, gamma_1pct=0.0, vega_usd=0.0, theta_usd=0.0)}
        result = check_limits(greeks, config, now_ct_time=datetime.time(10, 0), margin_utilization=0.0)
        assert not any(b.kind == "delta" for b in result)


# ---------------------------------------------------------------------------
# check_limits — gamma breach (no taper)
# ---------------------------------------------------------------------------

class TestCheckLimitsGamma:
    def test_long_gamma_breach(self):
        config = _make_config()
        greeks = {"dte_0": PortfolioGreeks(delta=0.0, gamma_1pct=5.0, vega_usd=0.0, theta_usd=0.0)}
        result = check_limits(greeks, config, now_ct_time=datetime.time(10, 0), margin_utilization=0.0)
        assert any(b.kind in ("gamma", "gamma_taper") for b in result)

    def test_gamma_at_limit_no_breach(self):
        config = _make_config()
        greeks = {"dte_0": PortfolioGreeks(delta=0.0, gamma_1pct=4.0, vega_usd=0.0, theta_usd=0.0)}
        result = check_limits(greeks, config, now_ct_time=datetime.time(10, 0), margin_utilization=0.0)
        assert not any(b.kind in ("gamma", "gamma_taper") for b in result)


# ---------------------------------------------------------------------------
# check_limits — 0DTE taper on naked short gamma
# ---------------------------------------------------------------------------

class TestCheckLimits0DTETaper:
    """Taper applies only to short gamma (gamma_1pct < 0) in dte_0."""

    def _greeks_short_gamma(self, gamma: float = -3.5) -> PortfolioGreeks:
        return PortfolioGreeks(delta=0.0, gamma_1pct=gamma, vega_usd=0.0, theta_usd=0.0)

    def test_before_taper_no_breach(self):
        # At 10:00, factor=1.0, limit=4.0; abs(-3.5)=3.5 < 4.0 → no breach
        config = _make_config()
        greeks = {"dte_0": self._greeks_short_gamma(-3.5)}
        result = check_limits(greeks, config, now_ct_time=datetime.time(10, 0), margin_utilization=0.0)
        assert not any(b.kind == "gamma_taper" for b in result)

    def test_after_taper_start_reduces_limit(self):
        # At 13:00, factor=0.5, effective limit=4.0*0.5=2.0; abs(-3.5)=3.5 > 2.0 → breach
        config = _make_config()
        greeks = {"dte_0": self._greeks_short_gamma(-3.5)}
        result = check_limits(greeks, config, now_ct_time=datetime.time(13, 0), margin_utilization=0.0)
        taper_breaches = [b for b in result if b.kind == "gamma_taper"]
        assert len(taper_breaches) == 1
        assert taper_breaches[0].severity == "hard"
        assert abs(taper_breaches[0].limit - 2.0) < 1e-12

    def test_after_14_hard_breach_always(self):
        # At 14:00, factor=0.0, effective limit=0.0; any short gamma = hard breach
        config = _make_config()
        greeks = {"dte_0": self._greeks_short_gamma(-0.001)}
        result = check_limits(greeks, config, now_ct_time=datetime.time(14, 0), margin_utilization=0.0)
        assert any(b.kind == "gamma_taper" and b.severity == "hard" for b in result)

    def test_after_15_also_hard_breach(self):
        config = _make_config()
        greeks = {"dte_0": self._greeks_short_gamma(-0.001)}
        result = check_limits(greeks, config, now_ct_time=datetime.time(15, 0), margin_utilization=0.0)
        assert any(b.kind == "gamma_taper" for b in result)

    def test_long_gamma_no_taper_applied(self):
        # Positive gamma_1pct: taper does NOT apply; uses plain gamma kind check
        config = _make_config()
        # 3.5 < 4.0 limit → no breach at all
        greeks = {"dte_0": PortfolioGreeks(delta=0.0, gamma_1pct=3.5, vega_usd=0.0, theta_usd=0.0)}
        result = check_limits(greeks, config, now_ct_time=datetime.time(14, 0), margin_utilization=0.0)
        assert not any(b.kind in ("gamma", "gamma_taper") for b in result)

    def test_taper_only_in_dte0_bucket(self):
        # Use dte_1_3 bucket — taper should NOT apply even for short gamma
        config = LimitsConfig(
            buckets={"dte_1_3": _make_bucket()},
            margin_utilization_soft=0.50,
            margin_utilization_hard=0.65,
            hedge_presence_required=True,
            taper=TaperConfig(
                start_ct=datetime.time(12, 0),
                zero_by_ct=datetime.time(14, 0),
                applies_to="naked_short_gamma",
                bucket="dte_0",   # taper is for dte_0, not dte_1_3
            ),
        )
        greeks = {"dte_1_3": PortfolioGreeks(delta=0.0, gamma_1pct=-3.5, vega_usd=0.0, theta_usd=0.0)}
        result = check_limits(greeks, config, now_ct_time=datetime.time(14, 0), margin_utilization=0.0)
        # abs(-3.5) = 3.5 < 4.0 limit → no breach; taper not applied
        assert not any(b.kind == "gamma_taper" for b in result)


# ---------------------------------------------------------------------------
# check_limits — vega breach
# ---------------------------------------------------------------------------

class TestCheckLimitsVega:
    def test_short_vega_breach(self):
        config = _make_config()
        greeks = {"dte_0": PortfolioGreeks(delta=0.0, gamma_1pct=0.0, vega_usd=-1500.0, theta_usd=0.0)}
        result = check_limits(greeks, config, now_ct_time=datetime.time(10, 0), margin_utilization=0.0)
        assert any(b.kind == "vega" for b in result)
        vega_b = next(b for b in result if b.kind == "vega")
        assert vega_b.severity == "hard"

    def test_long_vega_no_breach(self):
        config = _make_config()
        greeks = {"dte_0": PortfolioGreeks(delta=0.0, gamma_1pct=0.0, vega_usd=99999.0, theta_usd=0.0)}
        result = check_limits(greeks, config, now_ct_time=datetime.time(10, 0), margin_utilization=0.0)
        assert not any(b.kind == "vega" for b in result)

    def test_short_vega_at_limit_no_breach(self):
        config = _make_config()
        greeks = {"dte_0": PortfolioGreeks(delta=0.0, gamma_1pct=0.0, vega_usd=-1000.0, theta_usd=0.0)}
        result = check_limits(greeks, config, now_ct_time=datetime.time(10, 0), margin_utilization=0.0)
        assert not any(b.kind == "vega" for b in result)


# ---------------------------------------------------------------------------
# check_limits — theta breach
# ---------------------------------------------------------------------------

class TestCheckLimitsTheta:
    def test_theta_large_negative_breach(self):
        config = _make_config()
        greeks = {"dte_0": PortfolioGreeks(delta=0.0, gamma_1pct=0.0, vega_usd=0.0, theta_usd=-2500.0)}
        result = check_limits(greeks, config, now_ct_time=datetime.time(10, 0), margin_utilization=0.0)
        assert any(b.kind == "theta" for b in result)

    def test_theta_large_positive_breach(self):
        # Absolute value check — both directions
        config = _make_config()
        greeks = {"dte_0": PortfolioGreeks(delta=0.0, gamma_1pct=0.0, vega_usd=0.0, theta_usd=2500.0)}
        result = check_limits(greeks, config, now_ct_time=datetime.time(10, 0), margin_utilization=0.0)
        assert any(b.kind == "theta" for b in result)

    def test_theta_at_limit_no_breach(self):
        config = _make_config()
        greeks = {"dte_0": PortfolioGreeks(delta=0.0, gamma_1pct=0.0, vega_usd=0.0, theta_usd=-2000.0)}
        result = check_limits(greeks, config, now_ct_time=datetime.time(10, 0), margin_utilization=0.0)
        assert not any(b.kind == "theta" for b in result)


# ---------------------------------------------------------------------------
# check_limits — margin
# ---------------------------------------------------------------------------

class TestCheckLimitsMargin:
    def test_margin_below_soft_no_breach(self):
        config = _make_config()
        result = check_limits({}, config, now_ct_time=datetime.time(10, 0), margin_utilization=0.40)
        assert result == []

    def test_margin_at_soft_no_breach(self):
        # Boundary semantics: strictly-greater, so exactly at threshold does NOT breach.
        # Matches kill_switch_triggers.py "Boundary semantics (strict-greater)".
        config = _make_config()
        result = check_limits({}, config, now_ct_time=datetime.time(10, 0), margin_utilization=0.50)
        assert not any(b.kind in ("margin_soft", "margin_hard") for b in result)

    def test_margin_between_soft_and_hard_soft_breach(self):
        config = _make_config()
        result = check_limits({}, config, now_ct_time=datetime.time(10, 0), margin_utilization=0.57)
        assert any(b.kind == "margin_soft" and b.severity == "soft" for b in result)
        assert not any(b.kind == "margin_hard" for b in result)

    def test_margin_at_hard_soft_breach_only(self):
        # Boundary semantics: strictly-greater, so exactly at hard threshold does NOT
        # trigger a hard breach.  But 0.65 > 0.50 (soft), so a soft breach fires.
        # Matches kill_switch_triggers.py "Boundary semantics (strict-greater)".
        config = _make_config()
        result = check_limits({}, config, now_ct_time=datetime.time(10, 0), margin_utilization=0.65)
        assert not any(b.kind == "margin_hard" for b in result)
        assert any(b.kind == "margin_soft" and b.severity == "soft" for b in result)

    def test_margin_above_hard_hard_breach_only(self):
        config = _make_config()
        result = check_limits({}, config, now_ct_time=datetime.time(10, 0), margin_utilization=0.80)
        kinds = [b.kind for b in result]
        assert "margin_hard" in kinds
        # hard breach should NOT also generate a soft breach
        assert "margin_soft" not in kinds

    def test_breach_carries_correct_values(self):
        config = _make_config()
        result = check_limits({}, config, now_ct_time=datetime.time(10, 0), margin_utilization=0.70)
        hard = next(b for b in result if b.kind == "margin_hard")
        assert hard.bucket == "global"
        assert abs(hard.value - 0.70) < 1e-12
        assert abs(hard.limit - 0.65) < 1e-12


# ---------------------------------------------------------------------------
# hedge_presence_invariant
# ---------------------------------------------------------------------------

class TestHedgePresenceInvariant:
    def test_empty_book_passes(self):
        book = BookSnapshot(options=[], futures_qty=0.0)
        assert hedge_presence_invariant(book) is True

    def test_long_only_passes(self):
        book = BookSnapshot(
            options=[OptionPosition(expiry="2026-06-20", qty=2, right="C")],
            futures_qty=0.0,
        )
        assert hedge_presence_invariant(book) is True

    def test_naked_short_call_flagged(self):
        # Short call, no futures, no long in same expiry → naked
        book = BookSnapshot(
            options=[OptionPosition(expiry="2026-06-20", qty=-1, right="C")],
            futures_qty=0.0,
        )
        assert hedge_presence_invariant(book) is False

    def test_futures_hedged_short_passes(self):
        # Short call + futures hedge → invariant satisfied (v0)
        book = BookSnapshot(
            options=[OptionPosition(expiry="2026-06-20", qty=-1, right="C")],
            futures_qty=1.0,
        )
        assert hedge_presence_invariant(book) is True

    def test_long_option_hedge_in_same_expiry_passes(self):
        # Short call + long put in same expiry → not naked (long_qty > 0)
        book = BookSnapshot(
            options=[
                OptionPosition(expiry="2026-06-20", qty=-1, right="C"),
                OptionPosition(expiry="2026-06-20", qty=1, right="P"),
            ],
            futures_qty=0.0,
        )
        assert hedge_presence_invariant(book) is True

    def test_long_in_different_expiry_does_not_help(self):
        # Long in June expiry does NOT help June short:
        # Short in July is naked if no July long
        book = BookSnapshot(
            options=[
                OptionPosition(expiry="2026-07-18", qty=-1, right="C"),  # short July
                OptionPosition(expiry="2026-06-20", qty=2, right="C"),   # long June
            ],
            futures_qty=0.0,
        )
        # July expiry: net=-1, long_qty=0 → naked
        assert hedge_presence_invariant(book) is False

    def test_multiple_expiries_all_covered_passes(self):
        book = BookSnapshot(
            options=[
                OptionPosition(expiry="2026-06-20", qty=-1, right="C"),
                OptionPosition(expiry="2026-06-20", qty=2, right="P"),  # long hedge
                OptionPosition(expiry="2026-07-18", qty=-1, right="P"),
                OptionPosition(expiry="2026-07-18", qty=1, right="C"),  # long hedge
            ],
            futures_qty=0.0,
        )
        assert hedge_presence_invariant(book) is True

    def test_one_naked_among_several_expiries_fails(self):
        book = BookSnapshot(
            options=[
                OptionPosition(expiry="2026-06-20", qty=-1, right="C"),
                OptionPosition(expiry="2026-06-20", qty=2, right="P"),  # hedged
                OptionPosition(expiry="2026-07-18", qty=-1, right="C"),  # naked
            ],
            futures_qty=0.0,
        )
        assert hedge_presence_invariant(book) is False

    def test_net_long_after_partial_short_passes(self):
        # net qty = 3 - 1 = 2 (long) → not naked
        book = BookSnapshot(
            options=[
                OptionPosition(expiry="2026-06-20", qty=-1, right="C"),
                OptionPosition(expiry="2026-06-20", qty=3, right="C"),
            ],
            futures_qty=0.0,
        )
        assert hedge_presence_invariant(book) is True


# ---------------------------------------------------------------------------
# LimitsConfig.load — round-trip
# ---------------------------------------------------------------------------

class TestLimitsConfigLoad:
    """Load configs/risk/options_limits.yaml and verify key fields."""

    @staticmethod
    def _config_path() -> str:
        return str(_REPO / "configs" / "risk" / "options_limits.yaml")

    def test_load_round_trip_buckets(self):
        cfg = LimitsConfig.load(self._config_path())
        assert "dte_0" in cfg.buckets
        assert "dte_1_3" in cfg.buckets
        assert "weekly" in cfg.buckets
        assert "monthly" in cfg.buckets

    def test_load_dte0_values(self):
        cfg = LimitsConfig.load(self._config_path())
        b = cfg.buckets["dte_0"]
        assert b.max_abs_delta == 5.0
        assert b.max_abs_gamma == 2.0
        assert b.max_short_vega_usd == 500.0
        assert b.max_abs_theta_usd == 1000.0

    def test_load_global_margin(self):
        cfg = LimitsConfig.load(self._config_path())
        assert cfg.margin_utilization_soft == 0.50
        assert cfg.margin_utilization_hard == 0.65

    def test_load_hedge_required(self):
        cfg = LimitsConfig.load(self._config_path())
        assert cfg.hedge_presence_required is True

    def test_load_taper_fields(self):
        cfg = LimitsConfig.load(self._config_path())
        assert cfg.taper.start_ct == datetime.time(12, 0)
        assert cfg.taper.zero_by_ct == datetime.time(14, 0)
        assert cfg.taper.applies_to == "naked_short_gamma"
        assert cfg.taper.bucket == "dte_0"

    def test_loaded_config_produces_no_breach_for_clean_greeks(self):
        cfg = LimitsConfig.load(self._config_path())
        greeks = {
            "dte_0": PortfolioGreeks(delta=1.0, gamma_1pct=0.5, vega_usd=100.0, theta_usd=-50.0),
        }
        result = check_limits(greeks, cfg, now_ct_time=datetime.time(10, 0), margin_utilization=0.30)
        assert result == []


# ---------------------------------------------------------------------------
# LimitsConfig.load — malformed config fail-closed
# ---------------------------------------------------------------------------

class TestLimitsConfigLoadMalformed:
    """Missing or malformed keys must raise, not silently default."""

    @staticmethod
    def _write_yaml(content: str) -> str:
        """Write yaml content to a temp file and return its path."""
        fd, path = tempfile.mkstemp(suffix=".yaml")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_missing_bucket_key_raises(self):
        yaml_content = """\
# Missing max_abs_gamma
buckets:
  dte_0:
    max_abs_delta: 5.0
    max_short_vega_usd: 500.0
    max_abs_theta_usd: 1000.0
margin_utilization_soft: 0.50
margin_utilization_hard: 0.65
hedge_presence_required: true
taper:
  start_ct: "12:00"
  zero_by_ct: "14:00"
  applies_to: naked_short_gamma
  bucket: dte_0
"""
        path = self._write_yaml(yaml_content)
        with pytest.raises(KeyError):
            LimitsConfig.load(path)

    def test_missing_global_key_raises(self):
        yaml_content = """\
buckets:
  dte_0:
    max_abs_delta: 5.0
    max_abs_gamma: 2.0
    max_short_vega_usd: 500.0
    max_abs_theta_usd: 1000.0
# margin_utilization_soft missing
margin_utilization_hard: 0.65
hedge_presence_required: true
taper:
  start_ct: "12:00"
  zero_by_ct: "14:00"
  applies_to: naked_short_gamma
  bucket: dte_0
"""
        path = self._write_yaml(yaml_content)
        with pytest.raises(KeyError):
            LimitsConfig.load(path)

    def test_missing_taper_section_raises(self):
        yaml_content = """\
buckets:
  dte_0:
    max_abs_delta: 5.0
    max_abs_gamma: 2.0
    max_short_vega_usd: 500.0
    max_abs_theta_usd: 1000.0
margin_utilization_soft: 0.50
margin_utilization_hard: 0.65
hedge_presence_required: true
"""
        path = self._write_yaml(yaml_content)
        with pytest.raises(KeyError):
            LimitsConfig.load(path)

    def test_missing_taper_key_raises(self):
        yaml_content = """\
buckets:
  dte_0:
    max_abs_delta: 5.0
    max_abs_gamma: 2.0
    max_short_vega_usd: 500.0
    max_abs_theta_usd: 1000.0
margin_utilization_soft: 0.50
margin_utilization_hard: 0.65
hedge_presence_required: true
taper:
  start_ct: "12:00"
  # zero_by_ct missing
  applies_to: naked_short_gamma
  bucket: dte_0
"""
        path = self._write_yaml(yaml_content)
        with pytest.raises(KeyError):
            LimitsConfig.load(path)

    def test_missing_buckets_section_raises(self):
        yaml_content = """\
margin_utilization_soft: 0.50
margin_utilization_hard: 0.65
hedge_presence_required: true
taper:
  start_ct: "12:00"
  zero_by_ct: "14:00"
  applies_to: naked_short_gamma
  bucket: dte_0
"""
        path = self._write_yaml(yaml_content)
        with pytest.raises(KeyError):
            LimitsConfig.load(path)

    def test_unknown_bucket_name_raises(self):
        yaml_content = """\
buckets:
  bad_bucket_name:
    max_abs_delta: 5.0
    max_abs_gamma: 2.0
    max_short_vega_usd: 500.0
    max_abs_theta_usd: 1000.0
margin_utilization_soft: 0.50
margin_utilization_hard: 0.65
hedge_presence_required: true
taper:
  start_ct: "12:00"
  zero_by_ct: "14:00"
  applies_to: naked_short_gamma
  bucket: dte_0
"""
        path = self._write_yaml(yaml_content)
        with pytest.raises(ValueError):
            LimitsConfig.load(path)

    def test_inverted_margin_bounds_raises(self):
        yaml_content = """\
buckets:
  dte_0:
    max_abs_delta: 5.0
    max_abs_gamma: 2.0
    max_short_vega_usd: 500.0
    max_abs_theta_usd: 1000.0
margin_utilization_soft: 0.70
margin_utilization_hard: 0.50
hedge_presence_required: true
taper:
  start_ct: "12:00"
  zero_by_ct: "14:00"
  applies_to: naked_short_gamma
  bucket: dte_0
"""
        path = self._write_yaml(yaml_content)
        with pytest.raises(ValueError):
            LimitsConfig.load(path)

    def test_malformed_time_raises(self):
        yaml_content = """\
buckets:
  dte_0:
    max_abs_delta: 5.0
    max_abs_gamma: 2.0
    max_short_vega_usd: 500.0
    max_abs_theta_usd: 1000.0
margin_utilization_soft: 0.50
margin_utilization_hard: 0.65
hedge_presence_required: true
taper:
  start_ct: "25:99"
  zero_by_ct: "14:00"
  applies_to: naked_short_gamma
  bucket: dte_0
"""
        path = self._write_yaml(yaml_content)
        with pytest.raises(ValueError):
            LimitsConfig.load(path)
