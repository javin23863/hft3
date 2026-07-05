---
date: 2026-07-05
status: BOOK CLOSED — owner decision after IC run 4
program: event-alpha rebuild (EVENT_ALPHA_REBUILD_PLAN.md)
verdict: 9 models definitively no-edge at measured speed; 23 unjudgeable on owned data; program terminated before paid PR-5
successor_guidance: post-latency-shadow hypothesis classes; see "What to carry forward"
---

# Event-Alpha Program — Lessons Learned (closure document)

This is the honest record of what was tested, what killed it, what was never
tested, and what a future program should reuse. Every claim has a receipt.

## 1. Final scientific verdict

IC diagnostic run 4 (`runtime/stagec1/box_pull/ic_diag4/`, commit 486d4d76,
1,189 event tapes, Discovery 2018-2020 + Confirmation 2021-2022, holdout
2023-2024 sealed untouched):

| lane | verdict |
|---|---|
| 9 testable hypothesis models | **NO EDGE — killed, latency-robust** (2.6–7 ticks below cost line, one-sided p ≈ 1.0 vs their own hurdle) |
| 5 cross-asset lead-lag models | UNTESTED — 2–18 events vs 40-event floor (window starvation, not refutation) |
| 14 other hypothesis models | UNTESTED — same starvation |
| 4 VIX-conditioned models | UNTESTED — 0 events (VIX sensor coverage 342/1,205 + starvation) |
| Pass A campaign economics (−$168.7k, 0 promoted) | **STRUCK as evidence** — ran at fantasy latency (see §3); directionally confirmed worse under honest replay |
| Tox A/B (+$1.64/run gate benefit) | struck with Pass A |

Exploratory grid (2,241 cells): median −0.02 ticks. Fat-tail cells exist
(PRIOR_HIGH_LOW_BREAKOUT_TRAP on GDP/ISM +9–12 ticks, ES_NQ_DIVERGENCE_SNAPBACK
on RETAIL_SALES +13.8) but sit on single-digit event counts selected from
2,241 looks — recorded as *future hypotheses*, never findings.

## 2. The three measurement bugs that faked results before run 4

Each of these produced plausible-looking numbers that were garbage. A future
program should check all three on day one.

1. **One-sided-book mids.** `MBOFeatureExtractor` leaves `MID_PRICE = 0.0`
   when either book side is empty; event windows interleave real mids with
   zeros → forward "returns" of ±20,000 ticks. Run 1 reported edges of
   +1,120/−897 ticks — physically impossible, all artifact. Fix: a row is
   valid only when mid and spread are finite AND spread > 0
   (`build_ic_diagnostic.py`, PR #82). Corollary: **most run-1 "signal fires"
   happened on broken-book rows** — the models were detecting microstructure
   breakdown, not alpha (SECOND_WAVE events collapsed 199 → 25 after the gate).

2. **Fantasy latency.** The IC first used a flat 100 µs; the paid Pass A
   campaign injected a silent hardcoded 100 µs entry / 100 µs response —
   ~49× faster than the owner-measured 9.811 ms send→ack p99, below the
   repo's own replay band floor [0.5, 10] ms, via a code path that bypassed
   the validator (`run_hftbacktest_only_campaign.py:510-511`,
   `_resolve_latency_ns` constant branch). The smoking gun:
   FALSE_BREAKOUT_TRAP's "+4.5 tick edge" at 100 µs became +0.9 ticks at the
   measured mark — **the edge was the move that happens before our order can
   exist at the exchange.** Fixed in PRs #84/#85/#86: entry marked at
   exchange-arrival (measured offensive tick→send 60.9 µs + CC-3 decomposed
   send→exchange 3.595 ms ≈ 4 ms), band enforced on every path, receipts
   stamped, resume poisoned against pre-fix receipts.

3. **Test fixtures with wrong flag bits.** `tests/backtest_pipeline`
   `_event_contract` hand-rolls BUY=1<<10/SELL=1<<11/EXCH=1<<8/LOCAL=1<<9;
   real hftbacktest is 1<<29/1<<28/1<<31/1<<30. Synthetic tapes decoded
   all-bid → book never two-sided → tests exercised degenerate state without
   failing. Research tests overlay real constants
   (`test_ic_diagnostic_driver.py::_event_contract_via_pipeline`); the
   backtest-side fixtures were NOT fixed — known debt if that suite is revived.

## 3. Measured latency truth (the numbers that matter for any future strategy)

Source: `runtime/latency_reports/latency_truth.json` +
`latency_summary.json` + `reports/latency_baselines/live_r01_chicago/`
(live Rithmic 01, Chicago gateway, CHI404 colo, native C++ probe).

| component | p50 | p99 |
|---|---|---|
| offensive tick→decision | 0.96 µs | 5.2 µs |
| offensive tick→send | 27.3 µs | **60.9 µs** |
| defensive cancel→send | 13.1 µs | 18.9 µs |
| send→ack round trip (n=200, authoritative) | 3.54 ms | **9.811 ms** |
| CC-3 decomposition: send→exchange | — | 3.595 ms |
| CC-3 decomposition: exchange→ack | — | 1.793 ms |
| cancel→ack | **UNMEASURED** (all probes timed out far-from-market) | — |

Consequences, permanent:
- Our compute is world-class (sub-µs decision); **the wire+gateway is the
  budget** (~3.6 ms to exchange). Nothing sub-4 ms is capturable.
- Any strategy whose signal decays inside ~4 ms is structurally dead for us.
- Cancel→ack must be measured near-market before any passive/exit-sensitive
  strategy is trusted (currently proxied by send→ack, receipted).

## 4. Structural findings about event-time microstructure (data-verified)

- **Event half-spreads are 2.6–7 ticks** on MES in the −60s/+10s window —
  5–15× the quiet-market 0.5-tick half-spread. Signals that fire at event
  time pay event-time costs; every tested model's mid-move was smaller than
  the spread it would cross. Copeland-Galai/Glosten-Milgrom measured live.
- **The pre-registered horizons (15 s for 25/32 models) never fit the data**:
  tapes end +10 s post-release, so post-print fires cannot complete a 15 s
  label (SECOND_WAVE: 1,098 events censor-excluded vs 25 usable). The window
  spec (−60s/+10s, `packages/data_system/config/events.csv`) was chosen
  before the horizon map existed. **Lesson: window and horizon must be
  co-designed, mechanically, before purchase.**
- The doctrine's stage-2 window (10 s – 5 min post-print, depth rebuilt,
  speed irrelevant) **was never in the purchased data.** The catalog was
  killed in stage-1 conditions it was never designed for; the stage-2 theory
  remains unexamined. This was the deciding fact reviewed at closure; owner
  chose to stop rather than fund the WIDE re-purchase.

## 5. Statistical discipline that worked (keep verbatim in any future repo)

All implemented and test-locked in `packages/research_pipeline/ic_stats.py` +
`scripts/build_ic_diagnostic.py`:
- Effective N = events, never rows; two-way Cameron-Gelbach-Miller clustering
  (event × month), dof = min(clusters) − 1, non-positive-variance fallback.
- BH FDR over the FULL pre-registered family — no-verdict models stay in the
  denominator as p=1.0 (dropping them silently weakens the correction; caught
  by external review twice, at the stats layer and again at the call site).
- Hurdle-referenced inference: H0 is "does not clear its own cost line", not
  "different from zero" — a significant-but-below-hurdle model must not pass
  on its point estimate.
- DSR trials-deflation with num_trials = all parameter sets ever evaluated;
  constant-edge series degrade to NaN (float-noise variance yields Sharpe
  ~1e15 and DSR=1.0 — an auto-pass bug found by review).
- Pre-registration as code: horizon map git-blob gate (driver refuses dirty/
  uncommitted maps and any model absent from the map); holdout years refused
  at load with receipts; kill-list schema forbids exploratory-grid fields.
- Fail-soft ONLY for enumerated tape errors; everything else kills the run
  loudly (a bare `except` was converting pipeline regressions into "skipped
  tape" receipts — silent population shrinkage).

## 6. Process lessons

- **Review loops earn their cost**: external review (Greptile) found 6 real
  P1s in the IC stack (DSR invocation crash, constant-edge auto-pass, BH
  family shrinkage ×2, driver call-site bypass, npz path convention); 2 of 8
  findings were wrong and were refuted with executable repros (spread units,
  leader-preference ordering) — *verify empirically before fixing*.
- **Stacked-PR pitfall recurred**: #74 merged into its stack parent after the
  parent had already merged to main, stranding the spec pack off-main.
  Squash-merge stacks bottom-up, or re-land.
- **Delegation pattern that worked**: worktree-isolated subagents for bounded
  builds (specs, scaffolds, latency enforcement), read-only investigators for
  recon/audits, empirical refutation before accepting any external finding.
- **The owner's challenges were the highest-value reviews**: "are you using
  our measured speeds?" invalidated two runs and the paid campaign; "widen
  the window?" exposed that the data never contained the theory's arena.

## 7. What to carry forward to a future trading repo

Reusable as-is (paths in this repo):
- `packages/research_pipeline/ic_stats.py` — dependency-light stats core.
- `scripts/build_ic_diagnostic.py` — the gate pattern: one cheap, honest,
  execution-free test before any paid replay. See IC_GATE_RUNBOOK.md.
- `packages/backtest_pipeline/src/chi404_latency.py` + latency truth
  artifacts — measured-latency resolution + band law.
- `docs/hypotheses/HYPOTHESIS_SPEC_TEMPLATE.md` + 65 specs — the falsifiable
  spec discipline; several specs document degeneracies (PASSIVE_TRAP_FILL ≡
  SECOND_WAVE ≡ TRAILING_DRAWDOWN slice; FRIDAY/OVERNIGHT byte-identical) and
  structural no-ops (GHOST_ROUTE adapter reads absent slots, two `*0.0`
  models) — do not re-implement those without fixing the math.
- Data lake: see DATA_LAKE_HANDOFF.md.

Hypothesis classes with a mechanical reason to survive our 4 ms shadow
(untested here, candidates for a future program):
1. Stage-2 event structure (10 s–5 min post-print) — requires wider windows.
2. Cross-asset lead-lag residual on micros at multi-second horizons — leader
   plumbing already built and proven (88–95% coverage in run 4).
3. The exploratory tail cells as PRE-REGISTERED confirmations on fresh data:
   PRIOR_HIGH_LOW_BREAKOUT_TRAP × {GDP, ISM} × {0.5–15 s};
   ES_NQ_DIVERGENCE_SNAPBACK × RETAIL_SALES × 15 s.
4. Anything conditioned on the environment stack (VPIN/Hawkes/book-pressure
   compute in-stream via StructuralModelIntegrator) at horizons ≥ seconds.

What NOT to retry without new evidence: stage-1 post-print momentum/fade on
micros with taker entries — measured dead across 9 implementations, all
horizons ≤ 15 s, all vol regimes, both directions.
