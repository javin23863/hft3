"""Options-lane kill-switch trigger evaluation.

Design intent
-------------
Pure evaluation functions — NO transport, NO side effects, NO I/O beyond
reading the config dict that the caller supplies.  Runtime wiring (subscribing
to state updates, dispatching actions) is deferred to Phase 2 risk_engine
integration.

Each trigger function follows the same contract:

    evaluate_<trigger>(snapshot: OptionsSnapshot, cfg: dict) -> TriggerResult

Where:
  - snapshot  : OptionsSnapshot dataclass carrying the current risk state.
  - cfg       : parsed options_kill_switch.yaml dict (thresholds + trigger_actions).
  - return    : TriggerResult(fired, trigger_name, observed, threshold,
                              action_list, message).

Boundary semantics (strict-greater)
-------------------------------------
A trigger fires when ``observed > threshold`` (strictly greater).  At exactly
the threshold value the trigger does NOT fire.  This convention matches the
existing kill_switch.yaml triggers in the futures lane (max_daily_loss_breach
fires when loss > max_daily_loss).  All tests document this boundary.

Config loading
--------------
Use ``load_config(path)`` to parse options_kill_switch.yaml.  The loader uses
json (stdlib only) for JSON files or — if the caller has already parsed YAML —
accepts a plain dict.  For YAML files pass the dict directly from the caller
after parsing with PyYAML; this module does NOT import yaml to avoid a
hard dependency.  See ``load_config`` docstring.

Phase 2 note
------------
``halt_options_lane`` appears in action lists returned by fired triggers.  The
C++ runtime must implement this action as a lane-scoped halt with a
hedge_for_options carve-out (see options_kill_switch.yaml header comment).
Until Phase 2 the runtime should degrade to stop_new_orders + cancel_open_orders
on options instruments only.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TriggerResult:
    """Outcome of evaluating a single kill-switch trigger.

    Attributes
    ----------
    fired        : True if the trigger condition is satisfied.
    trigger_name : Canonical name matching the key in trigger_actions config.
    observed     : The value that was compared against the threshold
                   (None for boolean/compound conditions).
    threshold    : The configured threshold (None for boolean conditions).
    action_list  : Action list from config (empty list if not fired or
                   trigger absent from config).
    message      : Human-readable description of the evaluation outcome.
    """

    fired: bool
    trigger_name: str
    observed: float | bool | None
    threshold: float | bool | None
    action_list: list[str]
    message: str


# ---------------------------------------------------------------------------
# Snapshot dataclass
# ---------------------------------------------------------------------------


@dataclass
class OptionsSnapshot:
    """Minimal risk state snapshot consumed by trigger evaluation.

    All fields document units and semantics inline.  Fields are ``None``
    when the corresponding data is unavailable (triggers treat None as
    "condition not evaluable" and return fired=False with a warning message).

    Attributes
    ----------
    portfolio_vega_now       : Current portfolio vega ($ per vol point).
    portfolio_vega_60s_ago   : Portfolio vega 60 seconds ago ($ per vol point).
                               Used for vega_spike detection.
    portfolio_gamma          : Current portfolio gamma ($ per 1% underlying move²).
    portfolio_gamma_60s_ago  : Portfolio gamma 60 seconds ago.
                               Used for gamma_flip: fired when sign changes with
                               |gamma_now| > threshold.
    atm_iv_now               : Current ATM implied volatility (decimal, e.g. 0.20
                               for 20 vol).
    atm_iv_60s_ago           : ATM IV 60 seconds ago (same units).
    underlying_price_now     : Current underlying (futures) price.
    underlying_price_60s_ago : Underlying price 60 seconds ago.
    surface_age_s            : Age of the current volatility surface in seconds.
    is_rth                   : True if current time is Regular Trading Hours.
    stress_worst_cell_usd    : Worst stress-grid cell PnL (USD; negative = loss).
    hedge_invariant_ok       : True if every short option leg has a covering
                               hedge position or order.
    hedge_invariant_fail_s   : Seconds the hedge invariant has been continuously
                               violated (0.0 if currently satisfied).
    margin_utilization       : Current margin utilisation as a fraction [0, 1+].
    has_expiring_position    : True if the portfolio contains a position in an
                               instrument expiring today.
    has_management_order     : True if there is a management order for each
                               expiring position.
    past_expiry_cutoff       : True if current time is past the configured
                               expiry management cutoff.
    assignment_ledger_ok     : True if the post-expiry expected-delivery ledger
                               matches the broker record.  None if not yet in the
                               post-expiry reconciliation window.
    """

    portfolio_vega_now: float | None = None
    portfolio_vega_60s_ago: float | None = None
    portfolio_gamma: float | None = None
    portfolio_gamma_60s_ago: float | None = None
    atm_iv_now: float | None = None
    atm_iv_60s_ago: float | None = None
    underlying_price_now: float | None = None
    underlying_price_60s_ago: float | None = None
    surface_age_s: float | None = None
    is_rth: bool = True
    stress_worst_cell_usd: float | None = None
    hedge_invariant_ok: bool = True
    hedge_invariant_fail_s: float = 0.0
    margin_utilization: float | None = None
    has_expiring_position: bool = False
    has_management_order: bool = True
    past_expiry_cutoff: bool = False
    assignment_ledger_ok: bool | None = None


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_config(path_or_dict: str | dict[str, Any]) -> dict[str, Any]:
    """Load and validate the options_kill_switch config.

    Parameters
    ----------
    path_or_dict : Either a filesystem path to options_kill_switch.yaml
                   (parsed externally and passed as a dict) or a dict that
                   has already been parsed from YAML/JSON by the caller.
                   If a string path ending in ``.json`` is supplied the file
                   is loaded directly; YAML paths must be pre-parsed because
                   this module does not import PyYAML.

    Returns
    -------
    Validated config dict with at minimum the keys:
      ``thresholds``     (dict)
      ``trigger_actions`` (dict mapping trigger_name -> list[str])

    Raises
    ------
    ValueError  : if the config is structurally invalid (missing required keys,
                  wrong types).  Fail-closed: callers should treat a ValueError
                  as equivalent to all triggers fired.
    FileNotFoundError : if a path is given and the file does not exist.
    json.JSONDecodeError : if a .json path is given and the file is malformed.
    """
    if isinstance(path_or_dict, str):
        if not os.path.exists(path_or_dict):
            raise FileNotFoundError(
                f"options_kill_switch config not found: {path_or_dict}"
            )
        with open(path_or_dict, "r", encoding="utf-8") as fh:
            cfg: dict[str, Any] = json.load(fh)
    else:
        cfg = path_or_dict

    if not isinstance(cfg, dict):
        raise ValueError("Config root must be a mapping (dict).")
    if "thresholds" not in cfg:
        raise ValueError("Config missing required key: 'thresholds'")
    if "trigger_actions" not in cfg:
        raise ValueError("Config missing required key: 'trigger_actions'")
    if not isinstance(cfg["thresholds"], dict):
        raise ValueError("'thresholds' must be a mapping.")
    if not isinstance(cfg["trigger_actions"], dict):
        raise ValueError("'trigger_actions' must be a mapping.")

    return cfg


def _get_threshold(cfg: dict[str, Any], key: str) -> float:
    """Extract a numeric threshold from cfg['thresholds'], raising on missing key."""
    val = cfg["thresholds"].get(key)
    if val is None:
        raise ValueError(f"Config missing threshold key: '{key}'")
    return float(val)


def _get_actions(cfg: dict[str, Any], trigger_name: str) -> list[str]:
    """Return the action list for a trigger, or empty list if absent."""
    return list(cfg["trigger_actions"].get(trigger_name, []))


# ---------------------------------------------------------------------------
# Individual trigger evaluators
# ---------------------------------------------------------------------------


def evaluate_vega_spike(
    snapshot: OptionsSnapshot, cfg: dict[str, Any]
) -> TriggerResult:
    """Trigger 1: vega_spike.

    Fires when abs(portfolio_vega_now - portfolio_vega_60s_ago) > threshold.
    Boundary: strictly-greater fires.

    Returns fired=False (not evaluable) if either vega reading is None.
    """
    name = "vega_spike"
    threshold = _get_threshold(cfg, "vega_spike_abs_usd_per_vol_pt")
    actions = _get_actions(cfg, name)

    if snapshot.portfolio_vega_now is None or snapshot.portfolio_vega_60s_ago is None:
        return TriggerResult(
            fired=False, trigger_name=name,
            observed=None, threshold=threshold, action_list=[],
            message="vega data unavailable — trigger not evaluable",
        )

    observed = abs(snapshot.portfolio_vega_now - snapshot.portfolio_vega_60s_ago)
    fired = observed > threshold
    return TriggerResult(
        fired=fired, trigger_name=name,
        observed=observed, threshold=threshold,
        action_list=actions if fired else [],
        message=(
            f"|vega_delta|={observed:.2f} {'>' if fired else '<='} {threshold:.2f}"
        ),
    )


def evaluate_gamma_flip(
    snapshot: OptionsSnapshot, cfg: dict[str, Any]
) -> TriggerResult:
    """Trigger 2: gamma_flip.

    Fires when:
      - portfolio_gamma and portfolio_gamma_60s_ago have opposite signs, AND
      - |portfolio_gamma| > gamma_flip_min_abs_usd (strictly greater).

    The sign-flip condition is: gamma_now * gamma_60s_ago < 0.0.
    A zero crossing exactly at 0.0 is treated as a sign flip (product = 0).

    Boundary for magnitude check: strictly-greater fires.

    Returns fired=False if either gamma reading is None.
    """
    name = "gamma_flip"
    min_abs = _get_threshold(cfg, "gamma_flip_min_abs_usd")
    actions = _get_actions(cfg, name)

    if snapshot.portfolio_gamma is None or snapshot.portfolio_gamma_60s_ago is None:
        return TriggerResult(
            fired=False, trigger_name=name,
            observed=None, threshold=min_abs, action_list=[],
            message="gamma data unavailable — trigger not evaluable",
        )

    g_now = snapshot.portfolio_gamma
    g_prev = snapshot.portfolio_gamma_60s_ago
    sign_flipped = g_now * g_prev <= 0.0  # product <=0 means opposite signs or zero
    abs_now = abs(g_now)
    fired = sign_flipped and (abs_now > min_abs)

    return TriggerResult(
        fired=fired, trigger_name=name,
        observed=abs_now, threshold=min_abs,
        action_list=actions if fired else [],
        message=(
            f"gamma sign_flip={sign_flipped}, |gamma|={abs_now:.2f} "
            f"{'>' if abs_now > min_abs else '<='} {min_abs:.2f}"
        ),
    )


def evaluate_iv_dislocation(
    snapshot: OptionsSnapshot, cfg: dict[str, Any]
) -> TriggerResult:
    """Trigger 3: iv_dislocation.

    Fires when:
      - |atm_iv_now - atm_iv_60s_ago| > iv_dislocation_atm_vol_pts (strictly-greater)
      AND
      - |underlying_price_now/underlying_price_60s_ago - 1| <=
          iv_dislocation_underlying_move_pct
        (i.e. the IV move is NOT explained by an underlying move above the
         threshold; trigger fires on dislocation WITHOUT sufficient underlying move).

    Boundary: strictly-greater for IV move; strictly-less-than-or-equal for
    underlying move explanation test.

    Returns fired=False if any required field is None.
    """
    name = "iv_dislocation"
    iv_thresh = _get_threshold(cfg, "iv_dislocation_atm_vol_pts")
    ul_thresh = _get_threshold(cfg, "iv_dislocation_underlying_move_pct")
    actions = _get_actions(cfg, name)

    required = (
        snapshot.atm_iv_now,
        snapshot.atm_iv_60s_ago,
        snapshot.underlying_price_now,
        snapshot.underlying_price_60s_ago,
    )
    if any(v is None for v in required):
        return TriggerResult(
            fired=False, trigger_name=name,
            observed=None, threshold=iv_thresh, action_list=[],
            message="iv/underlying data unavailable — trigger not evaluable",
        )

    iv_move = abs(snapshot.atm_iv_now - snapshot.atm_iv_60s_ago)  # type: ignore[operator]

    ul_prev = snapshot.underlying_price_60s_ago
    assert ul_prev is not None
    if ul_prev == 0.0:
        ul_move_pct = math.inf
    else:
        ul_move_pct = abs(snapshot.underlying_price_now / ul_prev - 1.0)  # type: ignore[operator]

    large_iv_move = iv_move > iv_thresh
    ul_explains = ul_move_pct > ul_thresh  # underlying moved enough to explain IV
    fired = large_iv_move and (not ul_explains)

    return TriggerResult(
        fired=fired, trigger_name=name,
        observed=iv_move, threshold=iv_thresh,
        action_list=actions if fired else [],
        message=(
            f"iv_move={iv_move:.4f} {'>' if large_iv_move else '<='} {iv_thresh:.4f}; "
            f"ul_move={ul_move_pct:.4f} {'explains' if ul_explains else 'does NOT explain'}"
        ),
    )


def evaluate_stale_surface(
    snapshot: OptionsSnapshot, cfg: dict[str, Any]
) -> TriggerResult:
    """Trigger 4: stale_surface.

    Fires when surface_age_s > stale_surface_max_age_s AND is_rth is True.
    Only enforced during Regular Trading Hours; outside RTH stale surfaces
    are expected (no live feed) and the trigger is suppressed.

    Boundary: strictly-greater fires.

    Returns fired=False if surface_age_s is None.
    """
    name = "stale_surface"
    max_age = _get_threshold(cfg, "stale_surface_max_age_s")
    actions = _get_actions(cfg, name)

    if snapshot.surface_age_s is None:
        return TriggerResult(
            fired=False, trigger_name=name,
            observed=None, threshold=max_age, action_list=[],
            message="surface_age_s unavailable — trigger not evaluable",
        )

    observed = snapshot.surface_age_s
    fired = snapshot.is_rth and (observed > max_age)

    return TriggerResult(
        fired=fired, trigger_name=name,
        observed=observed, threshold=max_age,
        action_list=actions if fired else [],
        message=(
            f"surface_age={observed:.1f}s {'>' if observed > max_age else '<='} "
            f"{max_age:.1f}s; is_rth={snapshot.is_rth}"
        ),
    )


def evaluate_stress_grid_breach(
    snapshot: OptionsSnapshot, cfg: dict[str, Any]
) -> TriggerResult:
    """Trigger 5: stress_grid_breach.

    Fires when stress_worst_cell_usd < -stress_grid_breach_usd.
    Equivalently: the worst-cell loss exceeds the hard limit.

    Boundary: strictly-less-than (loss is more negative than threshold) fires.
    Reported as observed = |worst_cell| for consistency with threshold sign.

    Returns fired=False if stress_worst_cell_usd is None.
    """
    name = "stress_grid_breach"
    breach_usd = _get_threshold(cfg, "stress_grid_breach_usd")
    actions = _get_actions(cfg, name)

    if snapshot.stress_worst_cell_usd is None:
        return TriggerResult(
            fired=False, trigger_name=name,
            observed=None, threshold=breach_usd, action_list=[],
            message="stress_worst_cell_usd unavailable — trigger not evaluable",
        )

    worst = snapshot.stress_worst_cell_usd
    observed_loss = -worst  # positive = loss amount
    fired = worst < -breach_usd  # loss exceeds hard limit (strictly)

    return TriggerResult(
        fired=fired, trigger_name=name,
        observed=observed_loss, threshold=breach_usd,
        action_list=actions if fired else [],
        message=(
            f"worst_cell={worst:.2f} USD; loss={observed_loss:.2f} "
            f"{'>' if fired else '<='} limit={breach_usd:.2f}"
        ),
    )


def evaluate_naked_short_without_hedge(
    snapshot: OptionsSnapshot, cfg: dict[str, Any]
) -> TriggerResult:
    """Trigger 6: naked_short_without_hedge.

    Fires when hedge_invariant_ok is False AND
    hedge_invariant_fail_s > naked_short_max_unhedged_s (strictly-greater).

    hedge_invariant_ok=True with fail_s=0 means the invariant is currently
    satisfied; no trigger.

    Boundary: strictly-greater fires on duration.
    """
    name = "naked_short_without_hedge"
    max_s = _get_threshold(cfg, "naked_short_max_unhedged_s")
    actions = _get_actions(cfg, name)

    observed = snapshot.hedge_invariant_fail_s
    invariant_violated = not snapshot.hedge_invariant_ok
    fired = invariant_violated and (observed > max_s)

    return TriggerResult(
        fired=fired, trigger_name=name,
        observed=observed, threshold=max_s,
        action_list=actions if fired else [],
        message=(
            f"hedge_invariant_ok={snapshot.hedge_invariant_ok}; "
            f"fail_duration={observed:.1f}s {'>' if observed > max_s else '<='} {max_s:.1f}s"
        ),
    )


def evaluate_margin_utilization_breach(
    snapshot: OptionsSnapshot, cfg: dict[str, Any]
) -> TriggerResult:
    """Trigger 7: margin_utilization_breach.

    Fires when margin_utilization > margin_utilization_hard_limit (strictly-greater).

    Boundary: strictly-greater fires.

    Returns fired=False if margin_utilization is None.
    """
    name = "margin_utilization_breach"
    hard_limit = _get_threshold(cfg, "margin_utilization_hard_limit")
    actions = _get_actions(cfg, name)

    if snapshot.margin_utilization is None:
        return TriggerResult(
            fired=False, trigger_name=name,
            observed=None, threshold=hard_limit, action_list=[],
            message="margin_utilization unavailable — trigger not evaluable",
        )

    observed = snapshot.margin_utilization
    fired = observed > hard_limit

    return TriggerResult(
        fired=fired, trigger_name=name,
        observed=observed, threshold=hard_limit,
        action_list=actions if fired else [],
        message=(
            f"margin_utilization={observed:.4f} {'>' if fired else '<='} "
            f"hard_limit={hard_limit:.4f}"
        ),
    )


def evaluate_expiring_position_unmanaged(
    snapshot: OptionsSnapshot, cfg: dict[str, Any]
) -> TriggerResult:
    """Trigger 8: expiring_position_unmanaged.

    Fires when ALL of:
      - has_expiring_position is True
      - past_expiry_cutoff is True  (current time is after configured cutoff)
      - has_management_order is False

    No numeric threshold comparison: condition is a conjunction of boolean flags.
    The cutoff time itself is enforced externally (caller sets past_expiry_cutoff).

    Observed/threshold are reported as None (boolean condition).
    """
    name = "expiring_position_unmanaged"
    actions = _get_actions(cfg, name)

    fired = (
        snapshot.has_expiring_position
        and snapshot.past_expiry_cutoff
        and (not snapshot.has_management_order)
    )

    return TriggerResult(
        fired=fired, trigger_name=name,
        observed=None, threshold=None,
        action_list=actions if fired else [],
        message=(
            f"has_expiring={snapshot.has_expiring_position}; "
            f"past_cutoff={snapshot.past_expiry_cutoff}; "
            f"has_mgmt_order={snapshot.has_management_order}"
        ),
    )


def evaluate_assignment_uncertainty(
    snapshot: OptionsSnapshot, cfg: dict[str, Any]
) -> TriggerResult:
    """Trigger 9: assignment_uncertainty.

    Fires when assignment_ledger_ok is explicitly False (post-expiry ledger
    mismatch detected).  None means not yet in post-expiry window — no trigger.

    This is a HARD HALT trigger: fires -> full action list including
    halt_options_lane + flatten_positions_if_configured.  The action list
    in config must always include these; see options_kill_switch.yaml.

    Observed/threshold reported as boolean for clarity.
    """
    name = "assignment_uncertainty"
    actions = _get_actions(cfg, name)

    ledger_ok = snapshot.assignment_ledger_ok
    # None = not in post-expiry window yet; False = mismatch; True = reconciled
    if ledger_ok is None:
        return TriggerResult(
            fired=False, trigger_name=name,
            observed=None, threshold=None, action_list=[],
            message="Not in post-expiry reconciliation window — trigger not evaluable",
        )

    fired = ledger_ok is False

    return TriggerResult(
        fired=fired, trigger_name=name,
        observed=(not ledger_ok), threshold=False,
        action_list=actions if fired else [],
        message=f"assignment_ledger_ok={ledger_ok}",
    )


# ---------------------------------------------------------------------------
# Aggregate evaluator
# ---------------------------------------------------------------------------


#: Ordered list of all trigger evaluator functions.
_ALL_EVALUATORS = [
    evaluate_vega_spike,
    evaluate_gamma_flip,
    evaluate_iv_dislocation,
    evaluate_stale_surface,
    evaluate_stress_grid_breach,
    evaluate_naked_short_without_hedge,
    evaluate_margin_utilization_breach,
    evaluate_expiring_position_unmanaged,
    evaluate_assignment_uncertainty,
]


def evaluate_all(
    snapshot: OptionsSnapshot,
    cfg: dict[str, Any],
) -> list[TriggerResult]:
    """Evaluate all 9 kill-switch triggers against the supplied snapshot.

    Parameters
    ----------
    snapshot : OptionsSnapshot with current risk state.
    cfg      : Parsed config dict from load_config().

    Returns
    -------
    List of TriggerResult for every trigger that fired (fired=True only).
    Empty list means no triggers fired.

    Notes
    -----
    - Evaluation is exhaustive: all triggers are checked regardless of earlier
      fires.  The caller can inspect the full list.
    - Order matches _ALL_EVALUATORS: vega_spike, gamma_flip, iv_dislocation,
      stale_surface, stress_grid_breach, naked_short_without_hedge,
      margin_utilization_breach, expiring_position_unmanaged,
      assignment_uncertainty.
    - Exceptions from individual evaluators are NOT swallowed.  Any threshold
      extraction error or type error propagates to the caller (fail-closed).
    """
    fired_results: list[TriggerResult] = []
    for evaluator in _ALL_EVALUATORS:
        result = evaluator(snapshot, cfg)
        if result.fired:
            fired_results.append(result)
    return fired_results
