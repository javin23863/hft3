# MANDATORY ONTOLOGY GATE: Before using this plan, operate from the Obsidian vault ontology, hft3 authority PDFs, official HftBacktest documentation, and the upstream `nkaz001/hftbacktest` repository. Do not invent replay, latency, queue, fill, or promotion methodology outside that authority.

# HftBacktest-Only Active Pipeline Plan

Status: accepted planning-control decision, implemented locally pending review-surface GrepLoop.
Date: 2026-06-29.

This document records the active direction: HftBacktest becomes the source of
truth for the current backtesting path. VectorBT is frozen as inactive
infrastructure for this plan. The repo now has an HftBacktest-only active-path
slice, Workbench HBT run visibility, and historical VectorBT/Stage A doc
reclassification.

## Authority And Supersession

Primary authorities checked for this plan:

| Authority | Binding |
|---|---|
| Obsidian `wiki/hot.md`, `Home.md`, `Memory Stack.md`, and `architecture/Agent Runtime Roadmap.md` | Load order, graph waiver, validation honesty, and PR GrepLoop discipline. |
| Repo `.cursor/rules/00-fable-mindset.mdc`, `docs/vault/FABLE_MINDSET.md`, and Fable public reference | Ground, reason, act, observe, verify, recover, and report honestly. |
| Repo `.cursor/rules/01-ponytail-mindset.mdc` and `docs/ai/PONYTAIL.md` | Minimal-diff, YAGNI, no new abstractions unless needed; never cut validation or finance/math invariants. |
| `docs/vault/HFTBACKTEST_LATENCY_ONTOLOGY.md` | HftBacktest latency must keep feed, order-entry, and order-response components separate. |
| `docs/project/HFTBACKTEST_REALISM_ENGINE_SPEC.md` | Official HftBacktest APIs, data validation, source lock, fill/queue, latency, and artifact contracts remain authoritative unless this plan explicitly replaces VectorBT handoff assumptions. |
| Obsidian `validation/Backtester Certification.md` and `library/System Implications.md` | Backtester claims require tiered certification, no-lookahead, event-time ordering, and robustness discipline. |
| Upstream HftBacktest repository and docs | Use official data schema, validation utilities, L2/L3 replay semantics, latency models, fill models, and v2 return-code semantics. |

This plan supersedes the active-path assumption in older documents that
HftBacktest receives only VectorBT promoted or robustness-qualified candidates.
Those documents remain historical implementation evidence until code/docs are
updated. The new active rule is:

```text
Raw CME MBO / tick data
  -> HftBacktest-normalized event data
  -> data, timestamp, and snapshot validation
  -> HftBacktest strategy run
  -> HftBacktest recorder, stats, and microstructure diagnostics
  -> post-HftBacktest evaluation
  -> reject / observe / promote
```
## Decision

Move the active pipeline to HftBacktest only.

VectorBT must not decide what HftBacktest receives. For this plan, VectorBT is
inactive infrastructure:

- no active VectorBT screening;
- no Stage A survivor dependency;
- no `--from-stage-a-survivors` dependency;
- no feature-store survivor cells as an execution gate;
- no `screening_artifact.json` promotion logic as the upstream selector;
- no bar-level prefiltering as a substitute for event replay;
- no economic promotion or rejection before HftBacktest output exists.

Pre-HftBacktest rejection is allowed only for admissibility failures:

- bad data or unreadable files;
- wrong HftBacktest dtype;
- invalid timestamp order or timestamp unit;
- missing or mismatched snapshot;
- wrong symbol, contract, tick, lot, or contract metadata;
- future-data contamination, including 2026 data while the project block remains active;
- strategy compile failure;
- runtime failure.

Everything before HftBacktest is data preparation and validation. Everything
after it is evaluation.

## Data Contract

The only admissible active input format is HftBacktest-compatible event data.
Every CME MBO event stream must normalize to the documented structured array
fields:

```text
ev        u64
exch_ts   i64
local_ts  i64
px        f64
qty       f64
order_id  u64
ival      i64
fval      f64
```

Preferred prepared layout:

```text
data/hbt/
  normalized/<symbol>/<date>/<event_id>_l3.npz
  snapshots/<symbol>/<date>/<event_id>_initial_snapshot.npz
  manifests/<symbol>/<date>/<event_id>_manifest.json
```

Each manifest must include at minimum:

```json
{
  "symbol": "ES",
  "contract": "ESH6",
  "event_id": "...",
  "source": "databento_cme_mbo",
  "raw_input": "...",
  "normalized_npz": "...",
  "initial_snapshot": "...",
  "start_ts_ns": 0,
  "end_ts_ns": 0,
  "tick_size": 0.25,
  "lot_size": 1,
  "contract_size": 50,
  "feed_type": "L3_MBO",
  "timezone": "UTC",
  "validation_status": "pass"
}
```

For CME MBO/L3 data, preserve `order_id` and use the L3 route. The preferred
path is:

```text
Databento CME MBO raw file
  -> hftbacktest.data.utils.databento.convert(...)
  -> *_l3.npz
  -> validate_event_order(...)
  -> BacktestAsset().data([...]).l3_fifo_queue_model()
```

L2/MBP queue models are fallback or comparison paths only. They must not be
reported as L3/MBO queue truth.

## Snapshot Contract

Snapshots initialize book state. They are not training rows and they are not the
backtest itself.

Correct event shape:

```text
initial snapshot: order book state immediately before the event window
event stream: every tick/order event inside the window
```

Incorrect event shape:

```text
snapshot timestamps treated as the run
bar simulation replacing tick/order replay
model rows replacing the market event stream
```

Default macro-event window remains controlled by event metadata, for example
`-60s` to `+10s` around CPI, NFP, FOMC, and related scheduled events when that
window is the approved event scope.

## Validation Contract

Validation is an admissibility gate, not a model-selection gate.

Required checks before any strategy run:

```text
1. HftBacktest dtype matches the eight-field event schema.
2. Exchange and local timestamps are nanoseconds.
3. EXCH_EVENT rows are ordered by exchange timestamp.
4. LOCAL_EVENT rows are ordered by local timestamp.
5. No uncorrected negative feed latency.
6. Event flags are valid and documented.
7. MBO/L3 rows preserve required order_id values.
8. tick_size, lot_size, and contract_size are verified.
9. Initial snapshot exists and matches symbol, contract, and window.
10. No future-data leakage, including 2026 data while blocked.
```

Use official HftBacktest validation and correction utilities where applicable,
including event-order validation. Any correction must write a receipt with
counts, method, and before/after hashes.

## Run Configuration Contract

Every HftBacktest run must explicitly declare:

```text
symbol
contract
event_id
event_window
normalized_npz
initial_snapshot
strategy_id
strategy_params
tick_size
lot_size
contract_size
fee_model
latency_model
entry_latency
response_latency
exchange_fill_model
queue_model
roi_lower_bound
roi_upper_bound
```

For CME MBO/L3, the base case is:

```python
asset = (
    BacktestAsset()
        .data([...])
        .initial_snapshot(...)
        .linear_asset(contract_size)
        .constant_order_latency(entry_latency_ns, response_latency_ns)
        .l3_fifo_queue_model()
        .no_partial_fill_exchange()
        .trading_qty_fee_model(maker_fee, taker_fee)
        .tick_size(tick_size)
        .lot_size(lot_size)
        .roi_lb(roi_lower_price)
        .roi_ub(roi_upper_price)
)
```

Use interpolated order latency when measured latency samples exist with the
required `req_ts`, `exch_ts`, and `resp_ts` fields. Constant latency is an
explicit baseline or fallback, not an implicit default.

## Latency Contract

Every run must keep these components separate:

- feed latency;
- order-entry latency;
- order-response latency.

Minimum acceptable constant-latency declaration:

```json
{
  "latency_model": "constant_order_latency",
  "entry_latency_ns": 100000,
  "response_latency_ns": 100000,
  "latency_sensitivity_ns": [50000, 100000, 250000, 500000, 1000000]
}
```

Preferred measured-latency declaration:

```json
{
  "latency_model": "intp_order_latency",
  "latency_data": "measured_order_latency.npz",
  "columns": ["req_ts", "exch_ts", "resp_ts"],
  "unit": "nanoseconds"
}
```

For hft3 execution-realism claims, CHI404 native C++ hot-path evidence remains
the latency authority. Workstation Python timings are informational only.

## Fill And Queue Contract

Every run must declare:

```text
exchange fill model
queue model
maker/taker fee model
rebate assumptions
partial-fill policy
max order size relative to displayed depth
market-impact status
```

Default CME MBO/L3 base:

```text
L3 data
l3_fifo_queue_model()
no_partial_fill_exchange()
explicit fee model
explicit tick and lot size
market_impact_mode = not_modeled or rejected when size is too large
```

Required sensitivities:

```text
no_partial_fill_exchange()
partial_fill_exchange()
latency low/base/high
fee/rebate base/conservative
queue/fill model sensitivity where data permits
```

Because HftBacktest is market-data replay, simulated orders do not alter the
historical book. Large liquidity-taking behavior must be rejected or labeled
`market_impact_not_modeled`.

## Strategy Interface

Every candidate model must be wrapped as a HftBacktest strategy. VectorBT
strategy objects and OHLC-bar execution substitutes are inactive for this path.

Required interface:

```text
input:
  hbt object
  recorder
  model parameters
  risk parameters
  optional PIT-safe signal arrays

loop:
  while hbt.elapse(interval_ns) == 0:
    clear inactive orders
    read hbt.depth(asset_no)
    read hbt.position(asset_no)
    read hbt.orders(asset_no)
    optionally read hbt.last_trades(asset_no)
    compute signal from PIT-safe state only
    submit/cancel/modify/hold
    record state
```

HftBacktest v2 return-code semantics are mandatory:

```python
while hbt.elapse(interval_ns) == 0:
    ...
```

Do not use:

```python
while hbt.elapse(interval_ns):
    ...
```

Order-related return codes must also be checked against success semantics and
recorded when they fail.

## Output Artifacts

Every active HftBacktest run writes:

```text
artifacts/hbt_runs/<run_id>/
  run_manifest.json
  data_manifest.json
  hbt_config.json
  strategy_config.json
  normalized_input_manifest.json
  recorder_result.npz
  stats_summary.json
  equity_curve.parquet
  orders.parquet
  fills.parquet
  position_timeseries.parquet
  latency_report.json
  fill_quality_report.json
  queue_diagnostics.json
  robustness_report.json
  promotion_decision.json
  audit.md
```

`promotion_decision.json` may be generated only after both
`recorder_result.npz` and `stats_summary.json` exist.

Custom diagnostics must include, as applicable:

- maker fill ratio;
- taker fill ratio;
- cancel-to-fill ratio;
- quote lifetime;
- queue wait time;
- adverse selection after fill;
- realized spread;
- implementation shortfall;
- latency sensitivity;
- fill-model sensitivity;
- symbol/event stability;
- PnL concentration by event;
- PnL concentration by timestamp bucket;
- max inventory excursion;
- inventory mean reversion;
- kill-switch violations.

## Evaluation Gates

No model is promoted, rejected for economics, or ranked until it has completed
HftBacktest.

Post-HftBacktest evaluation uses four gates.

### Gate 1: Mechanical Validity

```text
run completed without runtime error
all HftBacktest return codes handled
no timestamp/order-book failure
orders submitted/cancelled as expected
recorder output exists
stats output exists
```

### Gate 2: Economic Result

```text
positive net PnL after fees/rebates
acceptable drawdown
enough trades to evaluate
return per trade positive
no single-fill or single-event dependency
no impossible fill pattern
```

### Gate 3: Microstructure Realism

```text
fills survive latency sensitivity
fills survive queue/fill-model sensitivity
fills survive fee/rebate sensitivity
PnL is not caused by unrealistic liquidity taking
quote placement does not constantly cross the spread
inventory is bounded
cancel/replace behavior is plausible
```

### Gate 4: Robustness

```text
works across symbols where the theory says it should
works across multiple event dates
works across latency regimes
works across partial/no-partial fill assumptions
works across walk-forward partitions
does not rely on 2026 or future data
```

## Workbench Direction

The active workbench surface should reflect HftBacktest state only.

Hide or disable active-pipeline truth from:

- VectorBT tab;
- Stage A survivor tab;
- VectorBT screening artifact view;
- feature-store survivor view;
- bar-level selector;
- model selector disconnected from symbol/event runs.

Replace with:

- HBT Runs;
- HBT Data Validation;
- HBT Event Windows;
- HBT Strategy Results;
- HBT Fill Quality;
- HBT Latency Sensitivity;
- HBT Queue/Fill Sensitivity;
- HBT Promotion Decisions.

The workbench must answer:

```text
What symbol/event/model ran?
What data did it use?
Did the HftBacktest run complete?
What latency/fill/queue assumptions were used?
What orders were placed?
What fills occurred?
Was the edge real after costs?
Did it survive sensitivity?
Was it rejected, observed, or promoted?
```

## Smallest Implementation Sequence

Implementation progress as of 2026-06-29:

- Step 1 active CLI freeze landed in `scripts/run_pipeline.py`.
- Steps 2-4 initial active-path slice landed in
  `packages/backtest_pipeline/src/hftbacktest_only_pipeline.py` and
  `scripts/run_hftbacktest_only.py`.
- Step 5 Workbench active truth now uses the `hbt_runs` source backed by
  `artifacts/hbt_runs/<run_id>/`.
- Step 6 historical VectorBT/Stage A docs are reclassified as legacy or
  inactive where they previously described active-path prerequisites.

### Step 1: Freeze VectorBT Active Path

Do not delete VectorBT yet. Disable or bypass it from active CLI, workbench, and
pipeline truth.

Acceptance:

```text
A full active pipeline run can complete without importing VectorBT, calling
VectorBT, or reading VectorBT outputs.
```

### Step 2: Add HftBacktest Data Adapter

Create one canonical adapter:

```text
raw CME MBO -> HftBacktest L3 npz -> validation manifest
```

Acceptance:

```text
One CME symbol, one date, and one event window convert to valid HftBacktest npz.
```

### Step 3: Add HftBacktest Runner

Create one runner accepting:

```text
symbol
contract
event_id
normalized npz
initial snapshot
strategy_id
strategy params
latency config
fee config
fill config
queue config
```

Acceptance:

```text
One strategy runs end-to-end and writes recorder_result.npz and stats_summary.json.
```

### Step 4: Add Post-HBT Evaluator

No preselection. Evaluate only HftBacktest output.

Acceptance:

```text
promotion_decision.json is generated only after recorder_result.npz and
stats_summary.json exist.
```

### Step 5: Update Workbench

Make the frontend reflect backend truth.

Acceptance:

```text
Every displayed active result links to a real HftBacktest run_id and artifact
folder. No VectorBT result is shown as active pipeline truth.
```

### Step 6: Retire Or Reclassify Old Docs

Update older VectorBT-to-HftBacktest handoff docs so they are historical,
diagnostic, or inactive unless explicitly re-enabled.

Acceptance:

```text
Searches for active-path docs no longer present VectorBT survivors, Stage A, or
screening_artifact promotion as HftBacktest prerequisites.
```

## Required HFT Workflow

Every implementation slice under this plan uses the hft3 workflow from Fable,
Ponytail, and the vault roadmap.

```text
Fable
  -> Ponytail
  -> VaultGate
  -> GraphGate/GraphPre waived-by-owner-2026-06-16
  -> Spec
  -> Plan
  -> Minimal implementation slice
  -> Local preflight hygiene with rg
  -> Dual-pass review
  -> Scope-green verification
  -> Review-surface preparation when merge-ready is intended
  -> Plan Drift Review
  -> PR GrepLoop
```

Final two merge-readiness gates for this plan are:

```text
1. Plan Drift Review
2. PR GrepLoop
```

`PR GrepLoop` means the external PR/MR/CL AI review loop on a real review
surface, repeated until the current head is clean or the bounded stop is
reached. Local `rg` searches are mandatory local preflight hygiene, but they are
not GrepLoop.

Because graph gates are currently owner-waived, GraphPost is not a final gate
for this plan. Report `graph-gate: waived-by-owner-2026-06-16` until the owner
re-enables graph freshness requirements.

## Non-Goals

This plan does not:

- delete VectorBT code;
- claim existing HftBacktest campaign modules already satisfy the new active path;
- relax no-lookahead, walk-forward, data-order, or 2026/future-data blocks;
- allow workstation Python replay to claim production execution realism;
- route live or paper data/orders through the workstation;
- promote a model without HftBacktest artifacts, robustness evidence, and the
  required certification status.

## Acceptance Checklist

The HftBacktest-only active pipeline is accepted only when all are true:

```text
1. A full active run completes without VectorBT imports or VectorBT artifacts.
2. A CME MBO event window converts to valid HftBacktest L3 npz.
3. HftBacktest runs one candidate strategy end-to-end.
4. recorder_result.npz and stats_summary.json are written.
5. promotion_decision.json is generated only after HftBacktest output exists.
6. Workbench active truth shows only real HftBacktest run artifacts.
7. No Stage A survivor, VectorBT screener, or feature-store survivor cell
   controls the run.
8. Latency, fill, queue, fee, tick, lot, and market-impact assumptions are
   explicit in every run artifact.
9. Plan Drift Review passes against this document.
10. PR GrepLoop is last and clean on the current review head, or the handoff
    reports the exact unavailable or waived state with `merge-ready: no`.
```
