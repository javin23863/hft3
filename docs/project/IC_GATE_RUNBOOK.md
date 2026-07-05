---
date: 2026-07-05
status: canonical repeatable process (validated end-to-end runs 1-4, 2026-07-04/05)
purpose: test ANY model catalog for edge honestly, for ~$0.50 of compute, before any paid replay
---

# IC Gate Runbook — the repeatable model-validation pipeline

The pipeline that killed (or would have promoted) the event-alpha catalog.
Run it against any future catalog before spending on execution replays.
Cost: ~1–3 minutes on a 176-core box per full pass over ~1,200 event tapes.

## Phase 0 — preconditions (all enforced in code, not convention)

1. **Specs**: every model has a hypothesis spec per
   `docs/hypotheses/HYPOTHESIS_SPEC_TEMPLATE.md` §1-5 — mechanism (who pays
   us), exact formula TRANSCRIBED from source with file:line receipts,
   falsifiable prediction + refutation condition, cost hurdle, instrument
   binding. Registry entries carry `hypothesis_spec_ref`.
2. **Horizon pre-registration**: `docs/hypotheses/HORIZON_MAP_PREREGISTERED.json`
   — per-model H and threshold derived MECHANICALLY (modal envelope
   holding × step interval; zero researcher choice), committed to git. The
   driver refuses dirty/uncommitted maps and any model absent from the map.
   **Co-design H with the data window: every fired row needs ≥H of tape after
   it or the event censors out** (the mistake that starved 23/32 models here).
3. **Measured latency artifacts present**: `runtime/latency_reports/latency_summary.json`
   (+ optional CC-3 decomposition under `reports/latency_baselines/live_r01_chicago/`).
   The driver REFUSES to run unmeasured.
4. **Splits fixed in code**: Discovery (estimation only) / Confirmation
   (inference) / HOLDOUT (refused at load, receipted). Touch holdout exactly
   once, ever, after pre-registered pass criteria are committed.

## Phase 1 — run the gate

```bash
# workstation smoke first (always):
python -m pytest tests/research_pipeline/ -q --ignore=tests/research_pipeline/test_feature_family_e2e_smoke.py

# full run (box or workstation):
python scripts/build_ic_diagnostic.py \
  --campaign-manifest <manifest.jsonl> \
  --out-dir <out> \
  --leader-lake-root <dir-with-leader-tapes>   # optional: unlocks cross-asset legs \
  --workers <cores-16>
# entry latency: resolved automatically = measured offensive tick->send
#   + CC-3 send->exchange leg (falls back to authoritative send->ack RTT).
#   Override only with --entry-latency-ms (receipted as source=cli).
```

Box recipe (vast.ai): `git bundle create /tmp/x.bundle origin/main` →
`scp -P <port> ... :/data/ship/` → `git fetch /data/ship/x.bundle
refs/remotes/origin/main && git checkout FETCH_HEAD` → nohup the driver →
**verify the process actually started** (`pgrep` + log head) before polling.

## Phase 2 — read the outputs

- `ic_report.json`: `entry_latency_ms` + source (must be measured),
  `units_skipped` (every skip itemized — silence is not success),
  `leader_coverage` per cross-asset model, per-model per-horizon means,
  exploratory grid (labeled; NEVER feeds promotion), Discovery extras
  (vol terciles, barrier-k for expression design).
- `kill_list.json` (schema-locked to primary-family fields): per-model
  verdict `pass` / `fail` / `insufficient_events` /
  `no_verdict_leader_features_absent`.

`pass` requires ALL of: BH rejection (q=0.10, FULL pre-registered family,
hurdle-referenced one-sided null) AND spread-adjusted edge > pass line
(fee hurdle + 0.5 residual ticks; costs marked at entry time t+L) AND
≥40 Confirmation events AND censoring ≤20%.

## Phase 3 — sanity checks before believing ANY result

Run 1-4 postmortem checklist (each caught a real fake here):
1. **Edge magnitudes physical?** MES moves single-digit ticks in 15 s. If you
   see hundreds, mids are contaminated (one-sided books → check the
   spread>0 gate is active).
2. **Latency stamped and measured?** `entry_latency_source` must reference
   the measured artifacts, never a constant you assumed.
3. **Event counts vs run-over-run**: a big drop after a data-quality fix
   means earlier "fires" were on garbage state — treat earlier runs as void.
4. **units_skipped reasons**: only enumerated tape errors; any unexpected
   exception type = pipeline regression being masked.
5. **Synthetic-tape tests use REAL hftbacktest flag constants**
   (BUY=1<<29, SELL=1<<28, EXCH=1<<31, LOCAL=1<<30) or the book never forms.

## Phase 4 — decision rules (pre-committed)

- <2 passers → downstream execution work shrinks to passers.
- 0 passers → NO paid campaign. Pivot is data/features/new specs — never
  execution-parameter search (that's how Pass A burned $169k of sim PnL).
- Insufficient_events is NOT a kill — it's a data-design failure; fix the
  window/coverage or retire the model explicitly.

## Phase 5 — if anything passes: paid replay honestly

- Campaign runner now DEFAULTS to `--latency-model chi404_measured`
  (entry = send→exchange + offensive fire path ≈ 3.66 ms, response 1.79 ms);
  constant mode demands both values explicitly and band-validates [0.5,10] ms
  — no silent path remains (PR #85). Receipts per run incl.
  `cancel_latency_policy` (send→ack proxy until cancel→ack is measured).
- Execution expression v2 available: vol-scaled tick barriers frozen at entry
  (`pt_vol_mult`/`sl_vol_mult`, clamp [2,40] ticks, PIT EWMA λ=0.97) +
  `entry_hurdle_ticks` gate; legacy %-of-price path byte-parity locked
  (PR #79). k comes from the IC report's Discovery `sigma_k_median`.
- Pre-register holdout criteria in a committed doc BEFORE the holdout pass.

## Known debts (if this pipeline is revived)

- cancel→ack latency unmeasured (probe near-market; until then defensive
  exits are optimistic by an unknown amount).
- backtest-side test fixtures still carry wrong event-flag bits (§2.3 of
  LESSONS_LEARNED).
- VIX sensor coverage 342/1,205 events.
- 4 pre-existing red tests on main: test_feature_family_e2e_smoke (mock
  signature drift), test_hft_campaign_integration, test_latency_components ×2,
  test_pipeline_gate_report.
