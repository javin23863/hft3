"""Greek limits checker + 0DTE taper for the options lane.

Design notes
------------
Config loading
  PyYAML 6.0.3 is available in this environment (verified 2026-06-12).
  LimitsConfig.load() uses yaml.safe_load().  If PyYAML is unavailable at
  runtime the import raises ImportError with a clear message; no silent
  fallback to a broken state.

Thresholds
  All numeric limits in configs/risk/options_limits.yaml are PLACEHOLDERS
  pending WS-4 sizing studies and owner sign-off.  check_limits() enforces
  whatever values are present; it does not hard-code any threshold itself.

0DTE taper
  taper_factor(now_ct_time) is a pure function: it linearly scales the
  effective max_abs_gamma for naked short gamma in dte_0 from 1.0 at
  taper.start_ct down to 0.0 at taper.zero_by_ct (and stays 0.0 after).
  Before start_ct the factor is 1.0 (no taper).

Hedge presence invariant (v0)
  A position is 'naked short' if:
    - It holds at least one short option (net short in some (expiry, right)
      combination), AND
    - The book has zero futures positions, AND
    - There are zero long options in the same expiry (regardless of strike/right).
  This is a COARSE v0 definition: it does not check delta-neutrality or
  per-strike offsets.  Future versions should compute net delta per expiry
  and compare against a threshold.

Boundary semantics (strictly-greater)
  check_limits() reports a breach when observed > threshold (strictly greater).
  At exactly the threshold value no breach is reported.  This matches
  kill_switch_triggers.py (see its module docstring, "Boundary semantics
  (strict-greater)") so that both code paths agree at boundary values.

Breach severity
  - 'soft': advisory; does not require immediate action.
  - 'hard': must trigger stop-new-orders in the options lane risk engine.

Lane scope
  This module is options-lane only.  It is NOT imported by the live futures
  risk engine.  See configs/risk/options_limits.yaml header for merge path.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

try:
    import yaml as _yaml
except ImportError as _yaml_err:  # pragma: no cover
    raise ImportError(
        "PyYAML is required for options_risk.src.greeks_limits. "
        "Install it with: pip install pyyaml"
    ) from _yaml_err


# ---------------------------------------------------------------------------
# Required keys — fail-closed on missing config
# ---------------------------------------------------------------------------

_REQUIRED_BUCKET_KEYS: frozenset[str] = frozenset({
    "max_abs_delta",
    "max_abs_gamma",
    "max_short_vega_usd",
    "max_abs_theta_usd",
})

_REQUIRED_GLOBAL_KEYS: frozenset[str] = frozenset({
    "margin_utilization_soft",
    "margin_utilization_hard",
    "hedge_presence_required",
})

_REQUIRED_TAPER_KEYS: frozenset[str] = frozenset({
    "start_ct",
    "zero_by_ct",
    "applies_to",
    "bucket",
})

_KNOWN_BUCKETS: frozenset[str] = frozenset({"dte_0", "dte_1_3", "weekly", "monthly"})


# ---------------------------------------------------------------------------
# BucketLimits dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BucketLimits:
    """Greek limits for one DTE bucket.

    All values are PLACEHOLDERS from options_limits.yaml; see that file's
    header for the sign-off requirement.
    """

    max_abs_delta: float       # futures-equivalent contracts
    max_abs_gamma: float       # per 1% move, futures-equiv
    max_short_vega_usd: float  # positive number; limit on short (negative) vega
    max_abs_theta_usd: float   # positive number; limit on |theta|


# ---------------------------------------------------------------------------
# TaperConfig dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaperConfig:
    """0DTE taper settings loaded from the yaml taper section."""

    start_ct: datetime.time     # taper begins; factor = 1.0 at/before this time
    zero_by_ct: datetime.time   # factor reaches 0.0 at/after this time
    applies_to: str             # currently only 'naked_short_gamma'
    bucket: str                 # DTE bucket the taper applies to


# ---------------------------------------------------------------------------
# LimitsConfig
# ---------------------------------------------------------------------------


@dataclass
class LimitsConfig:
    """Parsed representation of configs/risk/options_limits.yaml.

    Load via LimitsConfig.load(path).  Direct construction is for tests.

    Attributes
    ----------
    buckets : dict mapping bucket name -> BucketLimits.
    margin_utilization_soft : soft-breach threshold for margin utilization.
    margin_utilization_hard : hard-breach threshold for margin utilization.
    hedge_presence_required : if True, hedge invariant is checked.
    taper : TaperConfig; 0DTE taper settings.
    """

    buckets: dict[str, BucketLimits]
    margin_utilization_soft: float
    margin_utilization_hard: float
    hedge_presence_required: bool
    taper: TaperConfig

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: str) -> "LimitsConfig":
        """Parse options_limits.yaml and return a LimitsConfig.

        Raises
        ------
        KeyError   : if a required key is missing (fail-closed).
        ValueError : if a value is malformed (e.g. wrong type, bad time).
        FileNotFoundError : if the file does not exist.
        """
        with open(path, "r", encoding="utf-8") as fh:
            raw: Any = _yaml.safe_load(fh)

        if not isinstance(raw, dict):
            raise ValueError(f"options_limits.yaml root must be a mapping; got {type(raw)}")

        # --- global keys --------------------------------------------------
        _check_required(raw, _REQUIRED_GLOBAL_KEYS, context="root")

        margin_soft = float(raw["margin_utilization_soft"])
        margin_hard = float(raw["margin_utilization_hard"])
        hedge_req = bool(raw["hedge_presence_required"])

        if not (0.0 <= margin_soft < margin_hard <= 1.0):
            raise ValueError(
                f"Expected 0 <= margin_utilization_soft ({margin_soft}) "
                f"< margin_utilization_hard ({margin_hard}) <= 1.0"
            )

        # --- buckets ------------------------------------------------------
        if "buckets" not in raw:
            raise KeyError("Missing required key 'buckets' in options_limits.yaml")

        raw_buckets: Any = raw["buckets"]
        if not isinstance(raw_buckets, dict):
            raise ValueError(f"'buckets' must be a mapping; got {type(raw_buckets)}")

        buckets: dict[str, BucketLimits] = {}
        for name, bucket_raw in raw_buckets.items():
            if name not in _KNOWN_BUCKETS:
                raise ValueError(
                    f"Unknown bucket '{name}'; expected one of {sorted(_KNOWN_BUCKETS)}"
                )
            _check_required(bucket_raw, _REQUIRED_BUCKET_KEYS, context=f"buckets.{name}")
            buckets[name] = BucketLimits(
                max_abs_delta=float(bucket_raw["max_abs_delta"]),
                max_abs_gamma=float(bucket_raw["max_abs_gamma"]),
                max_short_vega_usd=float(bucket_raw["max_short_vega_usd"]),
                max_abs_theta_usd=float(bucket_raw["max_abs_theta_usd"]),
            )

        # --- taper --------------------------------------------------------
        if "taper" not in raw:
            raise KeyError("Missing required key 'taper' in options_limits.yaml")

        raw_taper: Any = raw["taper"]
        _check_required(raw_taper, _REQUIRED_TAPER_KEYS, context="taper")

        taper = TaperConfig(
            start_ct=_parse_hhmm(raw_taper["start_ct"], "taper.start_ct"),
            zero_by_ct=_parse_hhmm(raw_taper["zero_by_ct"], "taper.zero_by_ct"),
            applies_to=str(raw_taper["applies_to"]),
            bucket=str(raw_taper["bucket"]),
        )

        return cls(
            buckets=buckets,
            margin_utilization_soft=margin_soft,
            margin_utilization_hard=margin_hard,
            hedge_presence_required=hedge_req,
            taper=taper,
        )


# ---------------------------------------------------------------------------
# PortfolioGreeks dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PortfolioGreeks:
    """Aggregated first-order Greeks for a single DTE bucket.

    delta       : signed net delta in futures-equivalent contracts.
    gamma_1pct  : signed net gamma per 1% underlying move, futures-equiv.
    vega_usd    : signed net vega in USD.  Negative = net short vega.
    theta_usd   : signed net theta in USD per calendar day.
    """

    delta: float
    gamma_1pct: float
    vega_usd: float
    theta_usd: float


# ---------------------------------------------------------------------------
# Breach dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Breach:
    """One limit violation detected by check_limits().

    Attributes
    ----------
    kind     : short string identifying the check (e.g. 'delta', 'gamma_taper').
    bucket   : DTE bucket name or 'global' for margin/hedge checks.
    value    : the observed value that triggered the breach.
    limit    : the limit value at the time of the check.
    severity : 'soft' (advisory) or 'hard' (stop-new-orders required).
    """

    kind: str
    bucket: str
    value: float
    limit: float
    severity: str  # 'soft' | 'hard'


# ---------------------------------------------------------------------------
# taper_factor — pure function
# ---------------------------------------------------------------------------


def taper_factor(now_ct: datetime.time) -> float:
    """Return the 0DTE naked-short-gamma taper factor in [0, 1].

    Parameters
    ----------
    now_ct : current Central Time as datetime.time.

    Returns
    -------
    1.0  before 12:00 CT (no taper).
    1.0  at exactly 12:00 CT.
    0.5  at 13:00 CT (midpoint).
    0.0  at 14:00 CT and after.

    The taper window 12:00–14:00 is loaded from TaperConfig but this
    function uses the canonical values matching options_limits.yaml.
    For a config-driven version, callers should use
    taper_factor_from_config(now_ct, taper_config).
    """
    return taper_factor_from_config(
        now_ct,
        _DEFAULT_TAPER_START,
        _DEFAULT_TAPER_ZERO,
    )


def taper_factor_from_config(
    now_ct: datetime.time,
    start: datetime.time,
    zero_by: datetime.time,
) -> float:
    """Config-driven linear taper factor.

    Returns
    -------
    float in [0.0, 1.0]:
      - 1.0 at or before start
      - 0.0 at or after zero_by
      - linearly interpolated between start and zero_by
    """
    # Convert to minutes since midnight for arithmetic
    now_m = _to_minutes(now_ct)
    start_m = _to_minutes(start)
    zero_m = _to_minutes(zero_by)

    if now_m <= start_m:
        return 1.0
    if now_m >= zero_m:
        return 0.0
    # Linear interpolation
    span = zero_m - start_m
    elapsed = now_m - start_m
    return 1.0 - elapsed / span


# Canonical taper boundaries matching options_limits.yaml defaults.
_DEFAULT_TAPER_START: datetime.time = datetime.time(12, 0)
_DEFAULT_TAPER_ZERO: datetime.time = datetime.time(14, 0)


# ---------------------------------------------------------------------------
# check_limits — main public function
# ---------------------------------------------------------------------------


def check_limits(
    bucketed_greeks: dict[str, PortfolioGreeks],
    config: LimitsConfig,
    *,
    now_ct_time: datetime.time,
    margin_utilization: float,
) -> list[Breach]:
    """Check all Greek limits and return a list of Breach objects.

    Parameters
    ----------
    bucketed_greeks   : dict mapping bucket name -> PortfolioGreeks.
                        Only buckets present in both this dict and
                        config.buckets are checked; extra buckets in
                        either are ignored.
    config            : LimitsConfig loaded from options_limits.yaml.
    now_ct_time       : current Central Time (datetime.time).
    margin_utilization: current margin utilization in [0, 1].

    Returns
    -------
    List of Breach objects.  Empty list = all limits satisfied.
    Hard breaches indicate stop-new-orders is required.
    Soft breaches are advisory.
    """
    breaches: list[Breach] = []

    # --- Greek limits per bucket ------------------------------------------
    for bucket, limits in config.buckets.items():
        if bucket not in bucketed_greeks:
            continue
        greeks = bucketed_greeks[bucket]

        # Delta
        abs_delta = abs(greeks.delta)
        if abs_delta > limits.max_abs_delta:
            breaches.append(Breach(
                kind="delta",
                bucket=bucket,
                value=abs_delta,
                limit=limits.max_abs_delta,
                severity="hard",
            ))

        # Gamma — apply taper for dte_0 naked short gamma
        abs_gamma = abs(greeks.gamma_1pct)
        effective_gamma_limit = limits.max_abs_gamma
        taper_kind = "gamma"

        if (
            bucket == config.taper.bucket
            and config.taper.applies_to == "naked_short_gamma"
            and greeks.gamma_1pct < 0  # short gamma
        ):
            factor = taper_factor_from_config(
                now_ct_time,
                config.taper.start_ct,
                config.taper.zero_by_ct,
            )
            effective_gamma_limit = limits.max_abs_gamma * factor
            taper_kind = "gamma_taper"

        if abs_gamma > effective_gamma_limit:
            breaches.append(Breach(
                kind=taper_kind,
                bucket=bucket,
                value=abs_gamma,
                limit=effective_gamma_limit,
                severity="hard",
            ))

        # Vega (short vega only: vega_usd < 0 means net short)
        if greeks.vega_usd < 0:
            short_vega_abs = abs(greeks.vega_usd)
            if short_vega_abs > limits.max_short_vega_usd:
                breaches.append(Breach(
                    kind="vega",
                    bucket=bucket,
                    value=-short_vega_abs,  # preserve sign in value
                    limit=-limits.max_short_vega_usd,
                    severity="hard",
                ))

        # Theta
        abs_theta = abs(greeks.theta_usd)
        if abs_theta > limits.max_abs_theta_usd:
            breaches.append(Breach(
                kind="theta",
                bucket=bucket,
                value=greeks.theta_usd,
                limit=limits.max_abs_theta_usd,
                severity="hard",
            ))

    # --- Margin utilization -----------------------------------------------
    if margin_utilization > config.margin_utilization_hard:
        breaches.append(Breach(
            kind="margin_hard",
            bucket="global",
            value=margin_utilization,
            limit=config.margin_utilization_hard,
            severity="hard",
        ))
    elif margin_utilization > config.margin_utilization_soft:
        breaches.append(Breach(
            kind="margin_soft",
            bucket="global",
            value=margin_utilization,
            limit=config.margin_utilization_soft,
            severity="soft",
        ))

    return breaches


# ---------------------------------------------------------------------------
# hedge_presence_invariant
# ---------------------------------------------------------------------------


@dataclass
class OptionPosition:
    """Minimal representation of a single option position for the invariant.

    Attributes
    ----------
    expiry  : expiry identifier (e.g. date string, int DTE bucket, any
              hashable); positions sharing an expiry are grouped together.
    qty     : signed quantity (positive = long, negative = short).
    right   : 'C' (call) or 'P' (put).  Not currently used in v0 logic
              (grouping is by expiry only, not expiry+right).
    """

    expiry: Any
    qty: float
    right: str  # 'C' | 'P'


@dataclass
class BookSnapshot:
    """Snapshot of a trader's book for hedge invariant checking.

    Attributes
    ----------
    options         : list of OptionPosition.
    futures_qty     : total signed futures quantity.  Zero means no futures
                      hedge.
    """

    options: list[OptionPosition]
    futures_qty: float


def hedge_presence_invariant(positions: BookSnapshot) -> bool:
    """Return True if the book satisfies the hedge presence invariant.

    A book FAILS (returns False) if ANY expiry has a net short option
    position AND the book has zero futures AND zero long options in that
    expiry.

    v0 definition of 'naked short':
      - An expiry group is naked-short if:
          net_qty_in_expiry < 0       (net short options in that expiry), AND
          positions.futures_qty == 0  (no futures hedge), AND
          long_qty_in_expiry == 0     (no long options in same expiry)
      This is COARSE: it does not compute delta-neutrality, does not check
      cross-expiry hedges, and does not differentiate calls vs puts for
      hedge credit.  Document and upgrade at WS-4.

    Parameters
    ----------
    positions : BookSnapshot with options list and futures_qty.

    Returns
    -------
    True  : invariant satisfied (book is acceptable under v0 definition).
    False : at least one naked short expiry found.

    Notes
    -----
    Long-only books always return True.
    Books with futures_qty != 0 always return True (futures hedge present).
    Empty books return True.
    """
    # If there is any futures hedge, invariant is satisfied globally (v0).
    if positions.futures_qty != 0.0:
        return True

    # Group by expiry: compute net qty and total long qty.
    net_by_expiry: dict[Any, float] = {}
    long_by_expiry: dict[Any, float] = {}

    for opt in positions.options:
        exp = opt.expiry
        net_by_expiry[exp] = net_by_expiry.get(exp, 0.0) + opt.qty
        if opt.qty > 0:
            long_by_expiry[exp] = long_by_expiry.get(exp, 0.0) + opt.qty

    for exp, net_qty in net_by_expiry.items():
        if net_qty < 0:
            # Net short this expiry
            long_qty = long_by_expiry.get(exp, 0.0)
            if long_qty == 0.0:
                # Naked short: no futures hedge, no long options in same expiry
                return False

    return True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_required(mapping: Any, keys: frozenset[str], context: str) -> None:
    """Raise KeyError if any key in keys is missing from mapping."""
    if not isinstance(mapping, dict):
        raise ValueError(f"Expected a mapping at '{context}'; got {type(mapping)}")
    for k in sorted(keys):
        if k not in mapping:
            raise KeyError(f"Missing required key '{k}' in section '{context}'")


def _parse_hhmm(value: Any, context: str) -> datetime.time:
    """Parse 'HH:MM' string to datetime.time.

    Raises ValueError for malformed input.
    """
    if not isinstance(value, str):
        raise ValueError(
            f"'{context}' must be a string in HH:MM format; got {type(value)}"
        )
    try:
        h, m = value.split(":")
        return datetime.time(int(h), int(m))
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"'{context}' could not be parsed as HH:MM: {value!r}"
        ) from exc


def _to_minutes(t: datetime.time) -> int:
    """Convert datetime.time to total minutes since midnight."""
    return t.hour * 60 + t.minute
