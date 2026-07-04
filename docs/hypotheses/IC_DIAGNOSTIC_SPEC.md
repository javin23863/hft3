# IC Diagnostic — pre-registered protocol (PR-1)

status: pre-registered (committed BEFORE any box run; the driver refuses a
dirty/untracked horizon map — enforcement is code, not convention)
plan: docs/project/EVENT_ALPHA_REBUILD_PLAN.md
driver: scripts/build_ic_diagnostic.py · stats: packages/research_pipeline/ic_stats.py

## Feature thesis
Pass A measured passive-fill PnL — a fill-selection-contaminated variable.
The catalog's actual claim (HYPOTHESIS_SPEC_TEMPLATE §3) is about MID moves:
`E[mid(t+H) − mid(t) | signal(t) > s] > hurdle`. This diagnostic measures that
claim directly, execution-free, and sorts the catalog into
dead / execution-fixable before any further paid replay.

## End-goal connection
Program gate: PR-3/PR-5 scope shrinks to PR-1 passers. **0 passers → PR-5 does
not run** and the next unit of work is data/features/new specs — not
execution code. (Committed while nobody knows the answer.)

## Literature basis
Cont-Kukanov-Stoikov 2014 (OFI→mid predictability — validity of the mid-move
instrument); Copeland-Galai 1983 / Glosten-Milgrom 1985 (why fill-conditioned
PnL is the wrong estimand); Benjamini-Hochberg 1995; Cameron-Gelbach-Miller
2011 (two-way clustering); Bailey-López de Prado (DSR).

## Data requirement
Prepared aoo tapes from the hbt_stagec3_a326db8f manifest (2,162 units).
Discovery 2018-2020 (estimation only), Confirmation 2021-2022 (all inference),
HOLDOUT 2023-2024 sealed in code (2023+ events refused + receipted).

## Implementation boundary
Read-only over tapes; one MarketStatePipeline pass per tape, all hypothesis
adapters; no replay, no orders, $0 economics. Lead-lag models abstain without
leader legs → `no_verdict_leader_features_absent` (first real test post-PR-2).

## Pre-registered parameters (mechanical, zero researcher choice)
- H per model: `HORIZON_MAP_PREREGISTERED.json` = modal envelope
  holding_period_bars × 1s interval. Blob id stamped into every receipt.
- s per model: modal envelope signal_threshold (same file).
- Primary family: 27 models × 1 (H, s) each, event-types/regimes POOLED.
- BH q = 0.10 (primary); DSR num_trials = envelope sets per model.
- Floors: ≥40 Confirmation events; ≥5 fired rows/event; censoring ≤20% at H*.
- Entry marking: edge = E[mid(t+H) − mid(t+L) | fired] with L = MEASURED
  order submit→ack latency (CHI404 native probe p99, ceil to int ms,
  runtime/latency_reports/latency_summary.json; driver refuses to run
  unmeasured). The first L ms of a move belong to faster participants —
  a flat 100µs assumption violated the CHI404 replay band [0.5, 10] ms
  and overstated momentum-class edges.
- Spread adjustment: edge − measured half-spread at ENTRY time (t+L);
  pass line = fee hurdle (instrument_specs/fee_model) + 0.5 residual ticks.
- Inference target: the clustered t (two-way CGM, event × month) is on the
  per-event NET series `edge_i − half_spread_i − pass_line` — H0 is "the
  hypothesis does not clear its own cost hurdle", matching the template claim
  E[move|signal] > hurdle. One-sided p (alternative: net > 0). A test against
  zero would let a significant-but-below-hurdle model pass on the point
  estimate alone.

## Testable behavior / acceptance gate
kill_list.json verdict per model: `pass` requires BH rejection of the
hurdle-referenced null AND spread-adjusted edge > pass line AND floors met. Everything else `fail` or an
explicit no-verdict reason. kill_list schema forbids exploratory-grid fields
(KILL_LIST_ALLOWED_FIELDS; test-locked). Exploratory grid (7 horizons ×
event-type × Discovery-frozen vol terciles) lives in ic_report.json,
labeled, and is never read by the PR-5 generator.

## Failure/rejection rule
- Dirty/untracked horizon map → refuse to run.
- Unparseable event year → hard fail (no silent bucket).
- <2 passers → PR-3/5 shrink; 0 passers → program pivots to data/specs.
- Tox A/B (report_tox_ab.py) is expression-v1-conditional: it cannot promote
  or kill anything; binding decision = paired arms under v2 in PR-5.
