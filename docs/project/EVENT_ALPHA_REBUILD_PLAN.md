---
date: 2026-07-04
status: approved
area: hftbacktest, ic-diagnostic, execution-expression, cross-asset, composition
repo_worktree: C:/Users/MSI/repos/hft3-fix-wt
branch: docs/spec-pack-active
owner_request: "IC-gated event-alpha rebuild via grep-loop PR workflow; battlefield doctrine; graded like a bad-mood grader; full 65-model spec backfill."
graph_gate: waived-by-owner-2026-06-16
predecessor: docs/project/HBT_CATALOG_SEMANTIC_ROUTING_FIX_PLAN.md
evidence_basis: runtime/stagec1/box_pull/hbt_stagec3_a326db8f/PASS_A_FULL_ECONOMICS.md
---

# Event-Alpha Rebuild — IC-Gated, Composition-Aware, GrepLoop PR Workflow

## Context — why this build

Campaign `hbt_stagec3_a326db8f` (first honest-semantics pass, 1,000,740 rows, 0 failed):
**0 promoted, aggregate net −$168,750**. Receipts localize the failure:

1. **Exit barriers mis-unit'd**: `stop_loss_pct`/`take_profit_pct` = % of entry price
   (`hftbacktest_only_pipeline.py:699,974-978`); grid 0.10–0.50% = 20–100 ticks on MES
   vs 5-bar holdings → **99.2% timeout exits** (max_holding 49,187 / TP 179 / SL 218).
   Research labeler uses ticks (`targets.py:84`) — unit mismatch with live strategy.
2. **Adverse selection measured**: default passive entry (`price_mode:"passive_best_bid_or_ask"`,
   `:2544`) → 36% of orders never filled; filled rows 28.5% win, mean −$1.83.
   (Copeland-Galai 1983; Glosten-Milgrom 1985; Fleming-Remolona 1999.)
3. **Strongest-prior lane never ran**: cross-asset lead-lag blocked `leader_tape_missing`
   (ES 25,944 / ES+NQ 12,972 / NQ 12,972 / ZN 12,972 rows) — plumbing complete
   end-to-end; ES/NQ/ZN units simply never prepared.
4. **The repo's own test never computed**: `HYPOTHESIS_SPEC_TEMPLATE.md:36`
   `E[mid(t+H) − mid(t) | signal(t) > s] > hurdle`. Pass A measured passive-fill PnL —
   a different, fill-contaminated variable.

Owner doctrine: battlefield framing; opponents (FPGA HFT) are faster — our equalizer is
mathematics. Environment must be known continuously (scanners), models compose
(defensive stack gates alpha), everything falsifiable, no corners.

## Opponent model (who is on the other side at an event → our counter)

| opponent | behavior | counter |
|---|---|---|
| MM quote-machines (Avellaneda-Stoikov class) | pull/widen pre-event, re-quote ms after print | never stand in the book through t0 (receipt: our resting orders died there); enter after state known, toxicity-clean |
| Snipers (Budish-Cramton-Shim 2015) | pick off stale quotes at t0 | taker-only in the jump window; no resting orders across prints |
| Stage-1 momentum HFT (Andersen et al. 2003) | take within 100ms–1s | concede stage 1; trade **stage 2** (1s–5min continuation/reversion after depth rebuilds — Fleming-Remolona) where math beats speed |
| Lead-lag arbitrageurs (Hasbrouck 1995/2003; Cont-Kukanov-Stoikov 2014) | enforce ES→MES in ms | micro-lag residual on micros is what they leave; the untested lead-lag lane targets exactly this |

Edge window = stage-2 event structure, conditioned on continuously computed environment
state (VPIN / Hawkes / book pressure — already computed in-stream by
`StructuralModelIntegrator`; this build wires them from advisory to gating).

## Workflow contract (repo law — every PR)

Spec (PROJECT_PLANNING_STANDARD 8 fields; hypotheses per HYPOTHESIS_SPEC_TEMPLATE §1-5,
cost hurdle = 2×fee/multiplier + slippage from `instrument_specs.py`, below-hurdle
rejected at intake) → branch `feat/|fix/` kebab, ≤80 files → cavecrew investigator→
builder→reviewer delegation → local `rg` preflight ≤3 iter (GREPLOOP.md) →
cavecrew-reviewer dual-pass (Pass A Karpathy; **Pass B: filtration, event-time,
no-lookahead, walk-forward Discovery 2018-2020 / Confirmation 2021-2022 /
HOLDOUT 2023-2024 SEALED / Recent 2025+, execution realism, regime posterior**) →
verify → `gh pr create` → Codex external review (`@codex` auto-workflow, ≤5 iter) →
merge at 0 🔴. Stacked-PR gate: A clean before B reviewed. Handoffs carry the
VALIDATION_HONESTY.md status block. Graph gate: waived-by-owner-2026-06-16.

---

## PR sequence

### PR-0 `docs/spec-pack` — anticipation math on paper first
Per-model HYPOTHESIS_SPEC_TEMPLATE §1-5: mechanism (who pays us), exact formula,
falsifiable prediction + REFUTATION condition, cost hurdle, class + instrument binding.
**Scope: full 65-model catalog (owner-selected)** — including defensive/context/RL
slugs (their specs state composition role + why they never trade standalone, matching
the semantic contracts shipped in `model_execution_contracts.py`). Split for review
load: PR-0a = 32 active (27 ran + 5 lead-lag; blocks PR-1), PR-0b = remaining 33
(parallel, non-blocking). Registry entries gain `hypothesis_spec_ref` links.
**Horizon pre-registration (grader fix #1):** per-model H committed here MECHANICALLY —
H = modal holding_period_bars × 1s step interval from the existing envelope
(measured distribution: 15000ms for 25 models, 5000ms/3000ms/1000ms for the rest —
see HORIZON_MAP_PREREGISTERED.json, the committed authority; zero researcher choice). PR-1's driver refuses to run if the horizon map's git blob is
not an ancestor of the run's commit.

### PR-1 `feat/ic-diagnostic` — THE GATE (~1,400 LOC, $0 replay spend)
Files: NEW `packages/research_pipeline/ic_stats.py` (~260, pure numpy/pandas — no
pipeline imports), NEW `scripts/build_ic_diagnostic.py` (~420), NEW
`scripts/report_tox_ab.py` (~160), NEW `docs/hypotheses/IC_DIAGNOSTIC_SPEC.md`,
tests (~480).

Mechanics: ONE `MarketStatePipeline` pass per tape evaluating ALL hypothesis adapters
(27× cheaper than per-model `build_meta_training_set` pass; parity test vs original) →
`build_labels_frame` reused verbatim (`targets.py:38-81`, 7 horizons 100ms–60s, ticks)
→ per-event conditional stats → report.

Statistics (grader fixes absorbed):
- **Inference on Confirmation (2021-2022) only** (fix #2); Discovery 2018-2020 for
  estimation + vol-tercile freezing only. Holdout seal enforced in code: 2023+ event
  rows → hard fail + exclusion receipt (test-proven).
- **Two-way clustered errors** (event_id × calendar month) in `clustered_t`;
  synthetic null tests include cross-event correlation (fix #3). Effective N = events
  (~150–300), never rows.
- **Primary family**: 27 models × pre-registered H × modal envelope threshold, pooled →
  BH FDR q=0.10 **+ DSR trials-deflation** via existing
  `research_pipeline/statistics.py:deflated_sharpe_ratio` with num_trials = all
  parameter sets ever evaluated per model (fix #2b).
- **Exploratory grid** (7 horizons × event-type × 3 vol-regimes): BH q=0.05, labeled
  `exploratory:true`; **kill_list.json schema forbids grid-derived fields** —
  PR-5 generator reads primary-family fields only, schema-tested (fix #4).
- **Spread-adjusted edge** (fix #5): report `E[edge − taker spread cost | fired]` using
  SPREAD slot 15 at signal time; pass line = edge > hurdle + execution cost per class.
  This bridges mid-space IC to execution-space PnL — the gap that killed Pass A.
- **Censoring control** (fix #9): per-horizon censoring rate per event-type; exclude
  events with >20% fired rows censored at H* (biases momentum down/reversion up).
- Kill rule: BH-pass AND spread-adjusted edge > hurdle at H* AND ≥40 contributing
  events AND ≥5 fired rows/event floor.

Hurdle authority (verified arithmetic): MES non-member all-in $0.52/side →
2×0.52/5.0 = 0.208 pts (0.83 ticks) + 1 tick taker slippage ≈ **1.83 ticks RT**;
ES: 0.0608 pts. Exact-equality unit tests.

Tox A/B from existing 138k receipts (zero compute): recompute `_parameter_hash` from
`envelope_rt_tox_ab.json`, pair base↔`_tox` by source_candidate_id, paired PnL diff.
**Labeled `expression-v1-conditional`** (fix #7) — binding tox decision re-runs as
paired arms under v2 expression in PR-5.

Tests prove: planted-IC recovery (±0.05), zero-IC null → 0 BH passes, clustering
correctness (row-inflation killed), BH + hurdle arithmetic exact, holdout seal
hard-fails, multi-adapter parity. Runtime: ~25-40 core-hours ≈ **15-30 min on the
208-core box**.

### PR-2 `feat/leader-lane-prepare` — unlock cross-asset (~530 LOC + ops)
NEW `scripts/build_leader_lake_manifest.py` (scan lake roots for ES/NQ/ZN npz →
`lake_manifest_c1.json` schema + coverage report vs MES/MNQ event universe), NEW
`scripts/prepare_leader_units.py` (existing `prepare_replay_data` path, full_l3 mode —
correct for leaders per PR #72; `_leader_unit_index` prefers full_l3), unblock-proof
receipt (blocker histogram diff per model), manifest-level test (synthetic units:
ES present → `cross_asset_npz` set + no blocker; absent → `leader_tape_missing:ES`).
**Numeric DoD before start (fix #10): ≥80% of each lane's 12,972 rows unblocked or the
remainder itemized as named raw-data gaps.** VIX extension via
`derive_vix_quotes_gap.py` chain: target coverage numbered after lake scan (342→N of
1,205), not "possibly." Disk check before prepare (ES full-depth 5-20× MES size).

### PR-3 `feat/execution-expression-v2` (~550 LOC; code parallel-safe, use gated on PR-1)
Edits confined to `_run_minimal_strategy` + `_strategy_surface_version`:
- New params `pt_vol_mult`/`sl_vol_mult` (+ `min/max_barrier_ticks` floor 2/cap 40,
  `vol_warmup_steps` 30): PIT EWMA vol of mid Δticks (λ=0.97) in the step loop,
  `sigma_H = sigma_step·√holding_steps`, barriers frozen at entry fill, written to
  receipt. **k fixed globally from PR-1 report (median |move at H*| in σ units), grid
  {±1 alternative} only** (fix #6 — no new parameter surface). Legacy % path untouched;
  byte-parity golden test.
- `entry_hurdle_ticks` gate at entry (incl. observed spread when crossing);
  skips counted `hurdle_skipped_entries`.
- Conflict of % and vol params → fail-closed `barrier_units_conflict`.
- Per-alpha-class defaults (momentum→cross_spread taker, reversion→passive+veto) live
  in the ENVELOPE, not pipeline defaults — preserves parameter_hash↔behavior contract.
- Surface version bump `...event_scan_v5_expression` → stale resume receipts invalid.

### PR-4 `feat/environment-composition` (~380 LOC; stacked after PR-3)
Minimal viable, wires what exists:
- Toxicity gate **required-explicit**: standalone params carrying no `toxicity_max_*`
  and no `toxicity_gate:"off"` → blocker `env_gate_unspecified` (default-ON delivered
  via envelope materialization, hash contract intact).
- Defensive veto: `defensive_veto_models:["QUOTE_PULL_BEFORE_VOLATILITY"]` — veto
  adapter evaluated on the SAME state/filtration in-loop, zeroes primary when fired;
  fail-closed if adapter unavailable; slug must be `blocks_trade`/defensive per
  `model_execution_contracts.contract_for` else blocker. Counters `veto_gated_out`;
  veto/no-veto paired arms in PR-5 envelope (a bad veto must be measurable).
- Environment receipt columns at entry (`vpin`, regime, hawkes cascade, book-pressure
  OFI; percentile computed only over the tape UP TO entry — no full-tape leak).
  Nothing gates on these columns yet; they feed later conditioning analysis.

### PR-5 verification campaign (paid, capped, pre-registered)
NEW `scripts/generate_expression_v2_envelope.py` (~220): consumes kill_list.json —
survivors only; holding from H*; vol-mult grid ≤ {k, k±1}; price_mode by alpha class;
toxicity ON + paired `_notox`/`_noveto` arms; `entry_hurdle_ticks` from FeeModel;
**≤12 sets/model; generator refuses >250k-row surface (hard spend cap)**.
`docs/reports/HOLDOUT_PREREG_<date>.md` committed BEFORE any holdout replay.
Gates: per-spec hurdle on Confirmation → existing `run_hbt_robustness_gate`
(DSR/PSR/PBO) → ONE sealed holdout pass (2023-2024) for survivors.

## Program-level kill criteria (fix #8 — committed now, before answers known)
- **<2 models pass PR-1 → PR-3/5 shrink to passers. 0 pass → PR-5 does not run;**
  next unit of work is data/features/new hypothesis specs — not execution code.
- Any PR: reviewer 🔴 > 0 or Codex actionable red → no merge (repo law).
- PR-5 spend hard-capped by generator; no mid-run scope growth.
- Tox A/B and veto arms judged only under v2 expression.

## Verification
- Per-PR scope-green: `python -m pytest tests/research_pipeline/ -q` (PR-1),
  `tests/backtest_pipeline/ -q` (PR-2/3/4); rg preflight receipts; dual-pass reviewer
  verdicts; Codex PR loop to 0 actionable.
- PR-1 smoke: 10-unit manifest end-to-end on workstation before box run.
- PR-2 DoD receipt: unblock_proof.json counts vs targets.
- PR-5: pre-registered pass criteria; holdout touched exactly once.

## Estimates
| PR | size | compute |
|---|---|---|
| 0a/0b spec pack | 65 specs (0a: 32 blocks PR-1; 0b: 33 parallel) | — |
| 1 IC diagnostic | ~1,400 LOC | 15-30 min box, ~$0.50 |
| 2 leader unlock | ~530 LOC + ops | prepare batch, ~1-2h box |
| 3 expression v2 | ~550 LOC | — |
| 4 env composition | ~380 LOC | — |
| 5 verification | ~220 LOC + run | ≤250k rows ≈ ¼ of Pass A, capped |
