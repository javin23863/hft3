# Options Expiry and Fixing — Daily Ops Runbook

**STATUS: DRAFT — owner review required before use in production.**

Plan reference: WS-3.4 (fixing VWAP engine), WS-3.5 (early exercise hygiene),
WS-5 (expiry handling)
Applies to: CME ES/NQ European weekly and EOM options (cash-settled at SOQ)
Companion runbooks: docs/runbooks/options_early_exercise_hygiene.md (American
quarterly FOPs)
Date drafted: 2026-06-12

---

## Overview

CME ES and NQ European weekly and end-of-month (EOM) options expire on their
designated expiry dates with final settlement equal to the Special Opening
Quotation (SOQ) — a 30-second VWAP of the underlying futures price from
14:59:30 to 15:00:00 CT ("the fixing"). This runbook covers the T-1 and
expiry-day operational steps required to manage positions through expiry.

For American-style quarterly FOPs (early exercise eligible), see the companion
runbook `docs/runbooks/options_early_exercise_hygiene.md`.

---

## T-1 Checks (day before expiry)

**Time: any time during the trading day, ideally before 16:00 CT.**

### 1. Expiry calendar entry check

Confirm the upcoming expiry date is in the `expiry_calendar` for the
relevant underlying:

```python
from options_data.src.expiry_calendar import expiries_between, fixing_datetime_utc
from datetime import date, timedelta

tomorrow = date.today() + timedelta(days=1)
hits = expiries_between(tomorrow, tomorrow)  # all kinds; returns [(date, ExpiryKind), ...]
if hits:
    fixing_utc = fixing_datetime_utc(tomorrow)
    kinds = ", ".join(kind.value for _, kind in hits)
    print(f"Expiry tomorrow ({kinds}). Fixing at {fixing_utc} UTC")
```

The calendar is shared for ES and NQ (no per-underlying parameter); restrict
to specific series with the `kinds=` argument (e.g.
`kinds=[ExpiryKind.WEEKLY_FRI, ExpiryKind.MONTHLY_EOM]`).

If the expiry date is not found and positions exist in expiring contracts,
this is a calendar gap — escalate before the expiry day begins.

Note: the expiry calendar in `options_data.src.expiry_calendar` has known
defects (see specs/OPTIONS_LANE.md ledger entry o-b):
- Launch dates for weekly/EOM series are approximate.
- STYLE_CONVERSION_DATE is approximate.
- Holiday table is v0.
Verify the result against the official CME expiry calendar at
cmegroup.com and against the current Databento GLBX.MDP3 instrument
definition for the expiring series.

### 2. Open interest (OI) snapshot

Pull an OI snapshot for all expiring options series held in the book:

- Source: Databento GLBX.MDP3 statistics schema (stat_type OPEN_INTEREST)
  or Rithmic market data open interest subscription.
- Record: OI per strike and series in the session log.
- Purpose: basis for post-expiry reconciliation; expected delivery calculation
  for in-the-money options.

Positions to check: any option whose expiry date = tomorrow AND whose underlying
matches an active position (ES, NQ, MES, MNQ).

### 3. Risk limit headroom check

Confirm all active options positions remain within the configured limits in
`configs/risk/options_limits.yaml`:

- `buckets.dte_0.*` limits will apply tomorrow. Verify current greek exposures
  for expiring positions fit within these limits.
- `taper.start_ct: "12:00"` — the gamma taper for dte_0 naked short gamma
  begins at 12:00 CT tomorrow. Ensure that any naked short gamma in expiring
  series is sized to reach zero by `taper.zero_by_ct: "14:00"`.

All threshold values in `options_limits.yaml` are PLACEHOLDER values pending
WS-4 calibration. Do not treat them as validated production constraints.

---

## Expiry-Day Timeline

### 12:00 CT — Taper start

The gamma taper defined in `configs/risk/options_limits.yaml` begins:

```yaml
taper:
  start_ct: "12:00"
  zero_by_ct: "14:00"
  applies_to: naked_short_gamma
  bucket: dte_0
```

At 12:00 CT the maximum allowed naked short gamma begins a linear ramp to
zero over the 12:00–14:00 CT window. Actively reduce or hedge naked short
gamma in expiring contracts during this window. All threshold values are
PLACEHOLDER — verify the taper parameters with the operator before using.

### 14:00 CT — Zero naked short gamma in expiring series

By 14:00 CT, all naked short gamma in expiring contracts must be zero. Any
remaining naked short gamma in dte_0 after 14:00 CT is a hard breach of
the `options_limits.yaml` taper configuration.

Kill-switch trigger in play:
- `options_kill_switch.yaml: naked_short_without_hedge` — fires if a naked
  short option leg exists without covering hedge.

### 14:55 CT — Rule 575 fixing-window pre-entry

**Rule 575 window begins in 5 minutes.** No aggressive underlying orders
are permitted in the 14:55–15:00:30 CT window while holding expiring options
exposure unless tagged `FIXING_REPLICATION_PASSIVE`.

Actions:
1. Compute net delta of all expiring options positions.
2. If delta hedge in underlying futures is needed: build the replication
   schedule via `packages/options_lane/src/fixing/vwap_engine.make_schedule`.
   Confirm `ReplicationSchedule.intent_tag == 'FIXING_REPLICATION_PASSIVE'`.
3. Cancel or hold any resting aggressive underlying orders that are NOT
   part of a replication schedule.

Reference: `docs/compliance/rule_575_fixing_window.md` for full control
specification and prohibited conduct list.

### 14:55–15:00:30 CT — Rule 575 window (restricted)

The ONLY permitted underlying activity in this window while holding
expiring options exposure:

- Passive replication schedules with `intent_tag = 'FIXING_REPLICATION_PASSIVE'`
  sized to hedge-replication need only.
- No aggressive orders crossing the spread with intent to influence the fixing.
- No spoofing, layering, or momentum ignition.

Kill-switch triggers in play during this window:
- `options_kill_switch.yaml: vega_spike` — halt on material rapid vega change.
- `options_kill_switch.yaml: iv_dislocation` — halt on unexplained ATM vol move.
- `options_kill_switch.yaml: stress_grid_breach` — halt on worst-case cell loss.
- `options_kill_switch.yaml: stale_surface` — halt on stale vol surface.

All trigger thresholds in `options_kill_switch.yaml` are PLACEHOLDER values.

### 15:00 CT — Fixing

The CME SOQ fixing is computed at 15:00 CT as the 30-second VWAP of futures
from 14:59:30 to 15:00:00 CT.

At 15:00 CT:
1. The replication window closes. All pending replication schedule slices
   after 15:00 are cancelled.
2. Record the achieved replication VWAP using
   `vwap_engine.vwap_from_trades(fills)`.
3. Record replication error in ticks using
   `vwap_engine.replication_error(fills, fixing_vwap, tick_size=0.25)`.
4. Log: fixing VWAP (from exchange/CME publication), achieved VWAP,
   replication error in ticks, total fills count and signed quantity.
5. Commence expected-delivery calculation: for each expiring option, compute
   expected cash settlement or delivery.

Kill-switch trigger in play:
- `options_kill_switch.yaml: expiring_position_unmanaged` — fires if an open
  position in an expiring contract exists after 15:00 CT with no management
  order. Threshold: `expiring_position_cutoff_ct: "15:00"` (PLACEHOLDER).

### 15:00–16:00 CT — Expected-delivery ledger reconciliation vs assignment

This window is for post-fixing reconciliation of expected settlement against
actual assignments.

Steps:

1. **Compute expected settlement** for each expiring option:
   - For each long European call: payout = max(fixing_vwap - K, 0) × multiplier
   - For each long European put: payout = max(K - fixing_vwap, 0) × multiplier
   - For short positions: reverse sign.
   - Sum all payouts by account; this is the expected cash settlement.

2. **Compare to FCM assignment notice** (if available during session or
   next-day clearing file). CME European options settle in cash; the FCM
   should provide settlement data. Rithmic: see ws0-7 question Q5 (exercise/
   assignment notifications — no confirmed API callback as of 2026-06-12;
   may require FCM clearing file or `replayPnl` at session start next day).

3. **Record the reconciliation outcome** in the session log:
   - Expected cash settlement (computed above).
   - Actual cash credit/debit from FCM (when available).
   - Discrepancy, if any.

4. **If discrepancy exists**: do not trade further until reconciled.
   Escalate to risk officer and FCM. Kill-switch trigger:
   - `options_kill_switch.yaml: assignment_uncertainty` — fires on
     post-expiry expected-delivery ledger mismatch (HARD HALT, mandatory
     incident).

Note: the OI join for post-expiry reconciliation (matching expiring OI to
expected deliveries) is currently stubbed in `fixing_window_study` with no
statistics backfill. This step relies on FCM clearing data in Phase 0–1.
See specs/OPTIONS_LANE.md ledger entry o-e.

### 16:00–17:00 CT — Delta gap check

After settlement confirmation, identify any residual delta exposures arising
from:
- Expiry settlement that left unexpected futures positions (European options
  settle in cash, so residual futures exposure is unexpected unless from an
  existing hedge leg that was not closed before the fixing).
- Any replication schedule slices that were not filled (fill shortfalls create
  a delta gap between the intended hedge and the actual replication).

Steps:
1. Compute residual delta = sum of all futures positions remaining in the book
   after settlement is confirmed.
2. Compare to intended post-expiry delta (typically zero after full hedge
   liquidation).
3. If residual delta exceeds the `options_limits.yaml buckets.dte_0.max_abs_delta`
   threshold: reduce or flatten.
4. Log delta gap and any remediation orders in the session log.

Kill-switch trigger in play:
- `options_kill_switch.yaml: naked_short_without_hedge` — residual short option
  exposure without hedge (American quarterly carryover; see early exercise
  runbook).

---

## Kill-Switch Triggers in Play on Expiry Day

All trigger names reference `configs/risk/options_kill_switch.yaml`. All
threshold values in that file are PLACEHOLDER pending calibration.

| Trigger | Condition | Action |
|---|---|---|
| `vega_spike` | Portfolio vega changes > `vega_spike_abs_usd_per_vol_pt` in 60s | stop_new_orders, cancel_open, halt_options_lane, incident |
| `gamma_flip` | Portfolio gamma crosses zero above `gamma_flip_min_abs_usd` | stop_new_orders, cancel_open, halt_options_lane, incident |
| `iv_dislocation` | ATM IV moves > `iv_dislocation_atm_vol_pts` without underlying move > `iv_dislocation_underlying_move_pct` | stop_new_orders, cancel_open, halt_options_lane, incident |
| `stale_surface` | Surface age > `stale_surface_max_age_s` during RTH | stop_new_orders, log |
| `stress_grid_breach` | Worst stress cell < -`stress_grid_breach_usd` | stop_new_orders, cancel_open, halt_options_lane, incident |
| `naked_short_without_hedge` | Naked short exists > `naked_short_max_unhedged_s` | flatten, halt_options_lane, incident |
| `margin_utilization_breach` | Margin utilization > `margin_utilization_hard_limit` | flatten, halt_options_lane, incident |
| `expiring_position_unmanaged` | Open position in expiring contract past `expiring_position_cutoff_ct` with no management order | cancel_open, halt_options_lane, incident |
| `assignment_uncertainty` | Post-expiry ledger mismatch (any) | flatten, HARD HALT, mandatory incident |

Note: `halt_options_lane` is a Phase 2 action. In Phase 0–1 it degrades to
`stop_new_orders + cancel_open_orders` on options instruments only. See
`options_kill_switch.yaml` action vocabulary comment.

---

## Links

- `docs/compliance/rule_575_fixing_window.md` — Rule 575 control specification
- `docs/runbooks/options_early_exercise_hygiene.md` — American quarterly FOPs
  (early exercise, assignment pre-planning)
- `packages/options_lane/src/fixing/vwap_engine.py` — VWAP replication engine
- `packages/options_data/src/expiry_calendar.py` — expiry calendar and fixing
  datetime utilities
- `configs/risk/options_kill_switch.yaml` — kill-switch triggers for options lane
- `configs/risk/options_limits.yaml` — greek limits and 0DTE taper
- `docs/ops/ws0-7-rithmic-question-list.md` — FCM/Rithmic open questions
  including exercise/assignment notification path (Q5)

---

## Open Items

- [ ] Owner review and sign-off required.
- [ ] Verify expiry calendar against CME official schedule and Databento
  instrument definitions (ledger o-b).
- [ ] Calibrate all threshold values in `options_kill_switch.yaml` and
  `options_limits.yaml` (all currently PLACEHOLDER).
- [ ] Resolve Rithmic exercise/assignment callback question (ws0-7 Q5) —
  determine whether FCM clearing file is the only notification path.
- [ ] Implement automated fixing-window monitoring (Phase 2, cross-reference
  Rule 575 compliance doc).
- [ ] Implement OI join for expected-delivery reconciliation (ledger o-e:
  currently stubbed, no statistics backfill).
- [ ] Confirm whether MES/MNQ micro options use the same SOQ fixing or a
  separate computation.
- [ ] Confirm 15:00:30 CT as the end of the Rule 575 monitoring window with
  FCM and compliance (currently based on operational convention, not
  published exchange guidance).
