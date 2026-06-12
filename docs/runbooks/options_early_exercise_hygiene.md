# Options Early Exercise Hygiene — Operational Runbook

**STATUS: DRAFT — owner review required before use in production.**

Plan reference: WS-3.5  
Module: `packages/options_lane/src/hedging/early_exercise.py`  
Tests: `tests/test_early_exercise.py`

---

## Purpose

American CME futures options (FOPs) can be exercised at any time before expiry.
This runbook covers two defensive directions:

- **Long positions**: detect when immediate exercise is optimal and act before
  the FCM cutoff.
- **Short positions**: anticipate counterparty assignment and pre-plan the
  resulting futures exposure.

This tool has ZERO trading alpha. It is purely defensive operations plumbing.

---

## Background: When Is Early Exercise Optimal for Futures Options?

For futures options under the Black-76 model (cost-of-carry b=0):

- At **r=0**: the early-exercise premium (EEP) is exactly zero. Exercise is
  never strictly optimal — the market price of holding always equals or exceeds
  intrinsic.
- At **r>0**: an option holder who exercises captures the intrinsic value in
  cash. That cash earns the risk-free rate. If the interest on intrinsic exceeds
  the remaining time value, immediate exercise is preferable.
- Practically: deep in-the-money, short-dated options at moderate rates are the
  primary candidates. ATM and OTM options essentially never satisfy the
  condition.

The LR-tree engine (`american.lr_tree_price`) encodes this via backward
induction. When the root node's continuation value falls to intrinsic, the tree
has already determined that immediate exercise is optimal.

---

## Daily Checks

### Pre-15:00 CT (before 15:00 Chicago time)

**Scan shorts for high assignment risk:**

```python
from options_lane.src.hedging.early_exercise import scan_chain

advisories = scan_chain(
    positions,          # list of Position (options_risk.stress_grid.Position)
    marks={"F": current_futures_price},
    r=current_overnight_rate,
)
for adv in advisories:
    if adv.side == "short" and adv.assignment_risk_level in ("high", "medium"):
        print(f"Position {adv.position_index}: {adv.action}")
        print(f"  TV remaining: {adv.advice.time_value_remaining:.4f} pts")
```

**Scan longs for optimal_now:**

```python
for adv in advisories:
    if adv.side == "long" and adv.advice.optimal_now:
        print(f"Position {adv.position_index}: {adv.action}")
        print(f"  Intrinsic: {adv.advice.intrinsic:.4f}  American: {adv.advice.american_value:.4f}")
```

### End of Day

Re-run the scan with the settlement futures price and updated rates.
Log advisory output to the session log.

---

## Advisory Actions

| Advisory | Condition | Action |
|---|---|---|
| `EXERCISE: contact FCM before cutoff` | Long position, `optimal_now=True` | Submit exercise instruction to FCM before contrary-instruction cutoff. See cutoff note below. |
| `ASSIGNMENT RISK HIGH: pre-plan futures hedge` | Short, `assignment_risk_level='high'` (TV < 2 ticks) | Expect assignment with high probability. Pre-plan futures hedge for delivered long/short futures position. |
| `ASSIGNMENT RISK MEDIUM: monitor closely` | Short, `assignment_risk_level='medium'` (TV < 6 ticks) | Monitor; assignment is possible if the market moves further ITM. |
| `HOLD` | All other cases | No action required. |

---

## Contrary-Instruction Cutoff

**UNVERIFIED. Must confirm with FCM.**

Per plan WS-3.5 the contrary-instruction cutoff is documented as **17:30 CT**
for CME quarterly FOPs. This has NOT been verified against the current FCM
agreement (see ws0-7-rithmic-question-list.md, question on exercise cutoffs).

**Before relying on this time: verify with AMP Futures or your FCM of record.**
Failure to submit exercise instructions before the cutoff results in automatic
exercise for ITM options by the OCC (ITM threshold: 0.01 index points or $0.50
per contract for ES). Auto-exercise may or may not be the desired behavior
depending on your position intent.

---

## Assignment: What Happens

When a short option is assigned:

- **Short call assigned**: you deliver a long futures position to the counterparty
  at the strike price. Net result: short futures at K. If current futures price F
  > K, you have an immediate loss of (F - K) per contract.
- **Short put assigned**: you are assigned a long futures position at the strike
  (you are forced to buy futures at K).
  Net result: long futures at K. If F < K, you have a mark-to-market gain of
  (K - F) per contract (but are now long futures).

In both cases you hold an unhedged futures position after assignment. The
futures leg must be hedged or closed promptly.

### Pre-Plan Hedge for Assigned Position

Before the `high` advisory materializes into an actual assignment:

1. Determine the futures symbol and quantity that will be delivered
   (one contract per option contract, same underlying).
2. Decide whether to offset immediately upon receiving assignment notice or to
   carry the futures position.
3. Pre-stage the offsetting order in the futures order management system.
   For CME ES: close via CME Globex; routing through Rithmic (CHI404 lane only).

---

## Parameter Defaults

The following defaults are **operational starting points only**. They are NOT
calibrated against historical CME exercise frequency. Operators should
back-test before relying on them.

| Parameter | Default | Description |
|---|---|---|
| `threshold_time_value_ticks` | 2 | High-risk threshold in ticks |
| `tick_size` | 0.25 | ES FOP tick size (index points) |
| `multiplier` | 50.0 | ES contract multiplier ($/point) |

Threshold in price points: `2 * 0.25 = 0.50` index points = $25/contract.  
Medium threshold: `6 * 0.25 = 1.50` index points = $75/contract.

---

## scan_chain marks Convention

`scan_chain` expects a single `"F"` key in the `marks` dict supplying the
current futures price applied uniformly to all positions:

```python
marks = {"F": 4650.25}   # current ES front-month futures price
```

All option positions in the book use this same futures price. For multi-expiry
books where different expiries may need different forward prices, call
`exercise_signal` directly with the appropriate F for each position.

---

## Integration with stress_grid.Position

`scan_chain` is compatible with `options_risk.src.stress_grid.Position`
directly — it checks `kind`, `style`, `qty`, `is_call`, `K`, `sigma`, `T`
fields, all of which are defined on that dataclass. If `options_risk` is
unavailable, any object matching the `PositionLike` protocol works.

---

## Links

- ws0-7-rithmic-question-list.md — FCM exercise cutoff, assignment notification
  timeline, margin treatment of assigned futures
- ws0-2-fcm-permissioning.md — FCM (AMP Futures) account permissions for
  exercise instructions
- ws0-1-rithmic-fop-capability.md — Rithmic FOP support confirmation
- `packages/options_pricing/src/american.py` — LR-tree engine (lr_tree_price)
- `packages/options_pricing/src/black76.py` — European baseline (b76_price)
- `packages/options_risk/src/stress_grid.py` — Position dataclass

---

## Status and Open Items

- [ ] **VERIFY** contrary-instruction cutoff time with FCM (see ws0-7).
- [ ] **CALIBRATE** threshold_time_value_ticks against historical CME FOP
  exercise data (minimum 1 year of quarterly expiry data).
- [ ] **TEST** scan_chain with live Rithmic FOP position feed before
  production use.
- [ ] **CONFIRM** OCC auto-exercise threshold for ES FOPs with current FCM.
- [ ] Owner review and sign-off required before this runbook is promoted
  from DRAFT.
