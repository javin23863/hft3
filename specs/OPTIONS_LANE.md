# OPTIONS_LANE.md — Options Lane Spec and Known-Defect Ledger

Version: 2026-06-12 (slice 1b, branch options/ws1-slice1)

---

## 1. Lane Charter

**Scope:** CME options on futures (FOPs) — ES and NQ weekly, daily, and
end-of-month European expirations, plus American quarterly FOPs. Instruments:
full-size ES/NQ and micro MES/MNQ options on CME Globex. Research and backtest
only in Phases 0–1. Shadow and live are future phases gated on the defect
ledger (§3) being EMPTY.

**What is NOT certified in this lane:**
- No volatility surface model is calibrated against live or historical CME data.
  Vol clock weights, expiry calendar rules, and margin scan ranges are
  placeholders pending WS-4 calibration studies.
- No live execution path exists. The Rithmic R|API+ options order path has not
  been exercised against a live or paper system; conformance testing has not
  been completed (see docs/ops/ws0-1-rithmic-fop-capability.md).
- No options-specific latency measurements exist. Live Rithmic ack latency for
  FUTURE_OPTION instruments is UNKNOWN (see specs/OPTIONS_LANE.md ledger o-i
  and runtime/latency_reports/latency_truth.json).
- Chain simulation (WS-3.3) does not have a validated futures-leg bridge or a
  calibrated fixing-VWAP volume profile.

**Embargo cross-reference:** vault decision 2026-06-12 — options lane approved
for research/backtest (Phases 0–1) only. Live-arm embargo is in effect until
the ledger below is EMPTY and Phase 2 gate conditions are met. See the hft3
Obsidian vault `decisions/` folder entry dated 2026-06-12.

**Phase status:**
- Phase 0: data ingestion and expiry calendar — IN PROGRESS (slice 1b).
- Phase 1: pricing, risk, and stress — IN PROGRESS (slice 1b).
- Phase 2: shadow trading with order records — BLOCKED on ledger EMPTY gate.
- Phase 3: live arm — BLOCKED on ledger EMPTY gate + Phase 2 completion.

**Execution during Phases 0–1:** ZERO execution. No orders are submitted to
any exchange, paper, or simulator system during Phases 0–1. All trading code
runs in backtest/replay mode only.

---

## 2. Architecture Reference

Key modules in this lane:

| Module | Purpose |
|---|---|
| `packages/options_data/src/expiry_calendar.py` | Expiry date and fixing datetime (DST-correct) |
| `packages/options_pricing/src/black76.py` | European Black-76 baseline pricer |
| `packages/options_pricing/src/american.py` | American LR-tree pricer |
| `packages/options_risk/src/stress_grid.py` | Stress scenario grid (numpy, no scipy) |
| `packages/options_risk/src/margin_approx.py` | SPAN margin approximation |
| `packages/options_lane/src/fixing/vwap_engine.py` | Fixing VWAP replication engine |
| `packages/options_lane/src/hedging/early_exercise.py` | Early exercise hygiene scanner |
| `packages/options_lane/src/backtest/chain_sim.py` | Options chain simulation |
| `configs/risk/options_kill_switch.yaml` | Kill-switch triggers (options lane) |
| `configs/risk/options_limits.yaml` | Greek limits and 0DTE taper |

Runbooks:
- `docs/runbooks/options_expiry_fixing.md` — daily expiry ops
- `docs/runbooks/options_early_exercise_hygiene.md` — American quarterly FOPs

Compliance:
- `docs/compliance/rule_575_fixing_window.md` — CME Rule 575 control
- `docs/compliance/rule_536b_timestamp_gap.md` — Rule 536.B timestamp gap

---

## 3. Lane-Scoped Known-Defect Ledger

**This ledger must be EMPTY before options lane live arm.**

Gate scope: this ledger is options-lane scoped. The CME futures lane ledger
lives in specs/CORRECTNESS.md §3. Neither lane's defects block the other
lane's arm. The EMPTY-before-arm requirement for the options lane is enforced
by the Phase 2 and Phase 3 gate checklists.

**Research/backtest (Phases 0–1) is NOT blocked by any item in this ledger.**
All items are gated on shadow or live arm only as noted.

| ID | Component | Description | Status |
|----|-----------|-------------|--------|
| o-a | `vol_clock` (options_pricing, chain_sim, stress_grid) | Volatility clock weights (`vol_clock`) are uncalibrated placeholders. All modules that consume vol_clock use a uniform or zero-default weight. No calibration against historical CME implied vol data has been performed. Any backtest or stress result that depends on vol_clock is a placeholder result. Fix in WS-4 (vol calibration study). | **OPEN** — blocks shadow/live arm. Research/backtest NOT blocked. |
| o-b | `expiry_calendar` (options_data) | Expiry calendar rules are UNVERIFIED against official CME definitions. Launch dates for CME weekly and daily option series are approximate. `STYLE_CONVERSION_DATE` (European→American style change for quarterly ES options) is approximate. The holiday table is v0 (generated programmatically, not verified against the CME holiday schedule). Any backtest that depends on expiry-date correctness for edge-case dates (holidays, early closes, new series launches) may have incorrect expiry assignments. Fix in WS-0/WS-5: verify all dates against current CME Rulebook and Databento GLBX.MDP3 instrument definition schedules. | **OPEN** — blocks shadow/live arm. Research/backtest NOT blocked but results near calendar boundaries should be treated with caution. |
| o-c | `margin_approx` (options_risk) | `price_scan_range` in `margin_approx.py` is uncalibrated — no CME SPAN parameter files have been obtained or processed. The margin approximation uses a fixed scan range (placeholder) rather than the exchange-published SPAN parameters. Additionally, the following SPAN inputs are not modeled: short option minimum charge, delta-scaling for deep OTM positions, inter-commodity spread credits, concentration charges. The margin estimate is a lower bound under normal conditions and may be materially wrong in high-vol regimes. Fix in Phase 2: obtain nightly CME SPAN parameter files via FCM and replace the approximation with a standards-compliant SPAN calculation. | **OPEN** — blocks shadow/live arm (margin-based kill-switch thresholds in `options_kill_switch.yaml` are unreliable until this is fixed). Research/backtest NOT blocked. |
| o-d | `chain_sim` + `vwap_engine` seams | The chain simulation (`chain_sim.py`) does not have a validated futures-leg bridge — the futures leg used for delta-hedging in simulated option spreads uses a stub price series rather than a matched front-month futures bar series from the data lake. Additionally, the fixing VWAP volume profile in `vwap_engine.py` is v0 (uniform prior, explicitly documented as a placeholder in the module docstring). The seam between chain_sim output and vwap_engine schedule input has not been integration-tested end-to-end. Latency for the fixing VWAP execution path is UNKNOWN (no measurement). Fix in WS-3.3 and WS-3.4 phase 2: replace stub futures series with matched lake data; calibrate volume profile from fixing_window_study output; measure execution latency. | **OPEN** — blocks shadow/live arm. Research/backtest NOT blocked. |
| o-e | `fixing_window_study` OI join | The OI join in `fixing_window_study` (post-expiry expected-delivery reconciliation) is stubbed: it reports the expected deliveries based on in-the-money positions but does not cross-reference against actual CME OI data to validate the computation. No historical statistics backfill is available — the reconciliation step in the expiry runbook (`docs/runbooks/options_expiry_fixing.md`) is manual for Phase 0–1. Fix in WS-5: implement OI join against Databento GLBX.MDP3 statistics schema; backfill at least one full quarterly expiry cycle. | **OPEN** — blocks shadow/live arm. Research/backtest NOT blocked. |
| o-f | `detector` calendar check | The expiry detector (`options_lane`) cross-checks expiry dates across chains, but the calendar check is limited to same-underlying chains (e.g., both legs of a calendar spread must be on the same underlying). Cross-underlying relationships (e.g., ES vs SPX-equivalent, or ES vs MES same expiry) are not checked. In practice this means a mixed-underlying book could have an expiry on one leg that is not flagged as expiry day for the combined position. Fix in WS-5: extend the detector to flag per-underlying expiry days independently for all open positions. | **OPEN** — blocks shadow/live arm. Research/backtest NOT blocked. |
| o-g | Butterfly (and spread) sufficiency check | The butterfly spread payoff check in `chain_sim` uses a sufficient-not-necessary condition to identify butterfly violations: it checks that the net premium is non-negative for a standard long butterfly, which is necessary but not sufficient to guarantee no-arbitrage. The Mingone (1995) sharp bounds for spread/butterfly no-arbitrage require additional slope and convexity checks across the full strike ladder. The current check will miss arbitrages that satisfy the simple condition but violate the slope bound. Fix in WS-3.3: implement Mingone sharp bounds (slope and convexity) as additional invariant tests. | **OPEN** — blocks shadow/live arm (model correctness). Research/backtest NOT blocked but butterfly pricing results should be treated with caution. |
| o-h | Rule 536.B dual-stamp gap | `packages/trade_manager/manager.py` method `_automatic_transition_timestamp` (lines 585–590) uses `time.monotonic_ns()` which is not wall-clock-synced. Order-transition records produced by `TradeManager` carry monotonic timestamps that cannot be expressed as calendar timestamps without a separately captured wall↔monotonic offset. This violates CME Rule 536.B (order-record timestamping) which requires wall-clock timestamps on all order records. See `docs/compliance/rule_536b_timestamp_gap.md` for the full remediation plan. No code change is made in this branch (research branch policy). | **OPEN** — blocks shadow/live arm (any phase that generates order records against exchange order IDs). Research/backtest NOT blocked. |
| o-i | Live Rithmic ack latency for FUTURE_OPTION | Live order-ack latency for CME FUTURE_OPTION instruments via Rithmic R|API+ Rithmic 01 (live system) is completely unknown. No measurement has been taken. `runtime/latency_reports/latency_truth.json` records `live_wire.status = "OPEN"`. The best available inference (TCP RTT from CHI404 to paper system endpoint) is not predictive of live FUTURE_OPTION ack latency under load. Any latency budget for 0DTE options quoting cancel-replace loops uses a PLACEHOLDER. Fix in WS-0: obtain Rithmic 01 credentials post-conformance and measure FUTURE_OPTION ack latency with canary orders; update `latency_truth.json`. | **OPEN** — blocks shadow/live arm. Research/backtest NOT blocked. |

---

## 4. Cross-References to Main Ledger

The following items in specs/CORRECTNESS.md §3 have specific relevance to the
options lane and are noted here for awareness (they are CME-lane items, not
options-lane items, and are managed in that ledger):

- `g` (TE estimator bias) — relevant if transfer entropy features are used for
  options flow signals. Status in main ledger: OPEN.
- `h` (BS Greek set incomplete) — directly relevant to options lane. `bs_delta`
  and `bs_call_price` are absent from `model_05_dealer_hedging.py`. Status in
  main ledger: OPEN.

These are tracked in CORRECTNESS.md; they are listed here so options lane
reviewers know they exist.

---

## 5. Gate: Ledger EMPTY Before Live Arm

The options lane ledger (§3 above) must be EMPTY (all items FIXED or
explicitly WAIVED by compliance review with documented rationale) before any
of the following:

- Phase 2 start (shadow trading with order records submitted to any system).
- Options live arm.

Items o-a through o-i are all currently OPEN. The ledger is NOT EMPTY.
Options live arm is blocked.
