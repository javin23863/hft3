# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent robustness methodology outside that authority.

# Robustness Pipeline Source Of Truth

Status: historical / inactive for active HftBacktest-only routing.
Implementation/data-contract source of truth for the legacy VectorBT to
HftBacktest robustness handoff. Active HBT runs follow
[HFTBACKTEST_ONLY_PIPELINE_PLAN.md](HFTBACKTEST_ONLY_PIPELINE_PLAN.md) and do not
require this handoff before replay.
For active HBT-only work, port only evidence-shape concepts such as raw
diagnostics, reason codes, readiness ledgers, and data-vs-pipeline audit after
rewriting them to
[HFTBACKTEST_ONLY_EVIDENCE_PARAMETER_SURFACE_PLAN.md](HFTBACKTEST_ONLY_EVIDENCE_PARAMETER_SURFACE_PLAN.md).

Last checked: 2026-06-24.

## Authority Stack

| Layer | Source | Binding rule |
|---|---|---|
| Vault state | Obsidian vault `wiki/hot.md`, `Home.md`, `Memory Stack.md`, `operations/2026-06-23 Pre-VastAI smoke handoff.md` | Current lane state is `VectorBT ready, robustness then HFB`; MSI is control plane only. |
| Robustness doctrine | `docs/project/ROBUSTNESS_TESTING_SPEC.md` | Defines required phases: parameter universe, VectorBT screen, surface robustness, regular walk-forward, WFC, DSR/PBO/CSCV, stress, HftBacktest replay. |
| VectorBT handoff | `docs/project/VECTORBT_SCREENING_ENGINE_SPEC.md` | VectorBT output is screening evidence only; replay-eligible rows must have fresh pass evidence for WFC, DSR, PBO, and CSCV. |
| HftBacktest handoff | `docs/project/HFTBACKTEST_REALISM_ENGINE_SPEC.md` | HftBacktest consumes only strict replay-eligible VectorBT rows; missing robustness evidence fails closed before replay. |
| Raw robustness assembler | `scripts/build_robustness_raw_inputs_from_screening.py` | Builds `hft3_robustness_raw_inputs_v1` only from complete measured screening-artifact parameter surfaces; fails closed on incomplete families, insufficient events/parameters, missing stress inputs, or invalid artifact binding. |
| Evidence packager | `scripts/package_robustness_evidence_inputs.py` | Converts raw robustness inputs into `hft3_robustness_evidence_inputs_v1`, binding candidate/artifact identity and normalizing source SHA256 receipts before application. |
| Evidence applicator | `scripts/apply_robustness_evidence_to_screening.py` | Consumes explicit evidence schema `hft3_robustness_evidence_inputs_v1`, checks bindings/receipts, recomputes artifact hash, and validates replay eligibility. |
| Robustness bridge | `packages/backtest_pipeline/src/robustness_bridge.py` | Calls existing WFC/DSR/PBO/CSCV/stress producers only when raw robustness inputs are supplied. It does not fabricate missing raw surfaces. |
| WFC evaluator | `apps/workbench/src/robustness/wfc/gate.py` | Evaluates full parameter-matrix IS/OOS correlation; `PASS` only becomes `wfc_status=pass`. |
| Producer library | `packages/research_pipeline/src/robustness_producers.py` | Supplies DSR, PBO/CSCV, bootstrap, Holm/BH, null, planted-alpha, adversarial, parameter-perturbation, and cost/latency stress calculations. |

## Pipeline Position

This pipeline position is legacy documentation. It is not active HBT-only
eligibility.

```text
Stage A survivors
  -> declared model, feature, parameter, data universe
  -> deterministic VectorBT / Vector VT screen
  -> screening_artifact.json
  -> build_robustness_raw_inputs_from_screening.py
  -> package_robustness_evidence_inputs.py
  -> apply_robustness_evidence_to_screening.py
  -> replay_eligibility_status=eligible
  -> HftBacktest / HFB realism replay
```

The robustness layer is after the VectorBT broad screen and before strict HftBacktest replay. It is not a replacement for either engine.

## Current Implementation State

| Capability | Status | Receipt |
|---|---|---|
| VectorBT smoke artifact | Exists on CHI404 from PR #13 smoke | Vault `wiki/hot.md`; `operations/2026-06-23 Pre-VastAI smoke handoff.md` |
| Strict replay eligibility in pilot artifact | 0 eligible rows | Pilot rows have not-run WFC/DSR/PBO/CSCV robustness evidence. |
| Raw robustness assembler CLI | Implemented | `scripts/build_robustness_raw_inputs_from_screening.py`; CHI404 focused verification `268 passed in 40.64s` on 2026-06-24. |
| Evidence packager CLI | Implemented | `scripts/package_robustness_evidence_inputs.py`; CHI404 focused verification included in the `268 passed in 40.64s` robustness handoff suite. |
| Evidence applicator CLI | Implemented | `scripts/apply_robustness_evidence_to_screening.py` |
| Robustness producer functions | Implemented as libraries | `packages/research_pipeline/src/robustness_producers.py`; `packages/backtest_pipeline/src/robustness_bridge.py` |
| Packageable raw robustness rows for the current PR #13 smoke artifact | 0 accepted families / 0 packageable candidates | CHI404 assembler diagnostic found 9 families, 0 accepted families, 0 packageable candidates, 1,168 candidate skips for `stress_decomposition_missing`, and 90,992 measured-metrics skips. Families failed for insufficient events and incomplete event-parameter surfaces. |

Do not fabricate or hand-wave this gap. The next testing step must produce or locate complete measured family surfaces plus explicit fee/tick stress inputs, run `scripts/build_robustness_raw_inputs_from_screening.py`, then package the resulting raw inputs with `scripts/package_robustness_evidence_inputs.py`.

## Data Contract

### 1. Clean Provenance Boundary

Inputs:

- code commit
- `screening_artifact_hash`
- `data_manifest_hash`
- `lake_manifest_hash`
- event CSV hash
- feature recipe hash
- parameter-space hash
- source file hashes for every robustness input

Use:

- prevents stale, partial, or mismatched evidence from being applied to a different artifact
- makes every replay-eligible row auditable back to the exact data and code used

Failure semantics:

- missing provenance is not `warn`; it is `not_eligible`
- source evidence must include a SHA256 receipt

### 2. Parameter Universe And Surface

Inputs:

- declared parameter names, types, bounds, units, and candidate values
- `parameter_space_hash`
- per-candidate `parameter_values_hash`
- same grid shape across IS/OOS surfaces

Use:

- defines the family of configurations actually tested
- prevents changing the search space after OOS results are visible
- gives WFC and CSCV a complete, comparable configuration universe

Failure semantics:

- a post-hoc parameter-space change creates a new family and cannot reuse old OOS evidence as if it were untouched
- isolated in-sample peaks are not enough for replay eligibility

### 3. VectorBT / Vector VT Screen

Inputs:

- event/feature data at the configured grain
- declared parameter universe
- fee/slippage assumptions used by the vector screen
- official VectorBT portfolio/stat outputs when available

Constructs:

- `screening_artifact.json`
- promoted and rejected rows
- `screening_status`
- `oos_expectancy`, `profit_factor`, Sharpe/Sortino/drawdown/trade metrics where available
- `not_run` sentinels for robustness fields not measured in the VectorBT pilot

Use:

- rejects weak models and weak parameter regions cheaply
- produces a measured artifact for downstream robustness work

Failure semantics:

- VectorBT can mark a row `screening_status=pass`
- VectorBT alone cannot mark a row strict replay-eligible unless fresh robustness evidence is already present

### 4. Surface Stability

Inputs:

- full parameter-surface metrics
- plateau/neighbor/cliff/sample-size measurements

Constructs:

- `surface_stability_metrics`
- formula-authority status proving the surface check is defined

Use:

- rejects fragile isolated peaks and unstable parameter neighborhoods

Failure semantics:

- replay eligibility requires surface status `pass`
- missing or formula-incomplete surface evidence stays non-eligible

### 5. Regular Walk-Forward

Inputs:

- time-ordered train/test folds
- fold-level IS and OOS metrics
- fold dates

Constructs:

- `walk_forward_metrics.fold_matrix`
- `walk_forward_metrics.fold_train_test_dates`
- `walk_forward_metrics.fold_metrics`
- `walk_forward_efficiency`
- `fold_dispersion`
- `is_oos_gap`
- `oos_decay`

Use:

- checks whether performance survives time-forward evaluation
- preserves the project invariant that later data cannot influence earlier tuning

Important distinction:

- the pilot `auxiliary_numpy_walk_forward` telemetry is not enough for strict replay eligibility
- strict regular walk-forward metrics are built from fold rows supplied to the robustness bridge

### 6. Walk Forward Correlation

Inputs:

- `wfc_rows`, one row per `parameter_hash` x `fold_id`
- each row includes `is_metrics` and `oos_metrics`
- optional `train_dates`, `test_dates`, `regime_label`, and `asset`
- `wfc_cfg` with threshold settings

Constructs:

- `wfc_status`
- `wfc_metrics.pearson`
- `wfc_metrics.spearman`
- `wfc_metrics.scatter_data`
- `wfc_metrics.quadrant_counts`
- `wfc_metrics.high_is_high_oos_region`
- `wfc_metrics.rejection_reason`

Use:

- checks whether the in-sample parameter surface predicts the out-of-sample parameter surface
- gates the model family before final parameter/plateau selection
- catches the case where one lucky OOS point survives but the surface relationship is random

Leakage rule:

- OOS surface values may be used for the WFC go/no-go diagnostic only
- OOS surface values may not be used to expand the parameter universe, change the feature recipe, or select parameters after the fact
- if WFC fails, no candidate advances to strict HftBacktest replay

### 7. DSR, Bootstrap, Stress, And Multiple-Testing Evidence

Inputs:

- `per_event_expectancies`
- `n_trials` for the full tested family
- `per_event_n_trades`
- `per_event_fee_per_rt`
- `per_event_tick_value`
- `p_values`
- perturbation settings

Constructs:

- `dsr_status`
- `bootstrap_ci_or_not_run`
- `dsr_or_not_run`
- `fee_stress_or_not_run`
- `slippage_stress_or_not_run`
- `latency_stress_or_not_run`
- `holm_bh_or_not_run`
- `null_battery_or_not_run`
- `planted_alpha_or_not_run`
- `adversarial_or_not_run`
- `parameter_perturbation_or_not_run`

Use:

- discounts inflated Sharpe caused by multiple testing and non-normal returns
- checks whether expectancy survives cost and latency stress
- runs negative and positive controls so the research system can reject noise and detect planted signal

Failure semantics:

- when expectancies are supplied, required stress/control producers must produce valid evidence
- missing decomposition arrays for fee/slippage/latency stress make the artifact stale
- `not_run` is allowed for pilot telemetry but not for replay-eligible candidates

### 8. PBO And CSCV

Inputs:

- `cscv_matrix`, shaped as blocks x configurations
- every configuration corresponds to the declared parameter family

Constructs:

- `pbo_status`
- `pbo_or_not_run`
- `cscv_status`
- `cscv_count_or_not_run`

Use:

- estimates probability of backtest overfitting across the tested strategy family
- uses combinatorially symmetric cross-validation structure to avoid relying on one favorable split

Failure semantics:

- `pbo_status=pass` requires PBO below the configured threshold
- `cscv_status=pass` requires CSCV structure to run and PBO to pass
- CSCV structure alone does not grant replay eligibility

### 9. Evidence Packaging

Apply-ready evidence must use schema `hft3_robustness_evidence_inputs_v1`.

Required per-candidate structure:

```json
{
  "schema": "hft3_robustness_evidence_inputs_v1",
  "candidates": {
    "<candidate_id>": {
      "binding": {
        "screening_artifact_hash": "<hash>",
        "candidate_id": "<candidate_id>",
        "parameter_values_hash": "<hash>",
        "feature_recipe_hash": "<hash>",
        "data_manifest_hash": "<hash>",
        "lake_manifest_hash": "<hash>"
      },
      "source_evidence": {
        "wfc_rows": {
          "path": "<artifact-path>",
          "sha256": "<64-hex-sha256>"
        }
      },
      "robustness_gate_scope": "screen|refine|full",
      "surface_stability_metrics": {},
      "robustness_input": {
        "per_event_expectancies": [],
        "n_trials": 0,
        "cscv_matrix": [],
        "wfc_rows": [],
        "wfc_cfg": {},
        "per_event_n_trades": [],
        "per_event_fee_per_rt": [],
        "per_event_tick_value": [],
        "p_values": []
      }
    }
  }
}
```

Use:

- binds robustness inputs to the exact screening artifact and candidate row
- gives the applicator enough raw data to recompute statuses through `compute_robustness_evidence()`
- persists a `robustness_evidence_receipt` in the updated screening artifact

Failure semantics:

- binding mismatch, missing source hash, malformed source evidence, or stale/failing computed evidence yields `not_eligible`
- the CLI refuses to overwrite the source artifact
- `--min-eligible 1` must pass before using the output for HftBacktest

### 10. Replay Eligibility

A promoted row can become strict replay-eligible only when all are true:

- `screening_status=pass`
- `wfc_status=pass`
- `dsr_status=pass`
- `pbo_status=pass`
- `cscv_status=pass`
- `robustness_artifact_staleness=fresh`
- surface stability status is `pass`
- required non-status maps are not `not_run`
- `validate_candidate_replay_eligibility(row)` returns no errors

Only then can `hft_generate_campaign_manifest.py` and HftBacktest/HFB smoke testing consume the candidate without the transitional path.

## Literature And Documentation Receipts

| Pipeline piece | Receipt | How hft3 uses it |
|---|---|---|
| Multiple testing and DSR | Bailey and Lopez de Prado, "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality", SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551 | `dsr_status`, `dsr_or_not_run`, `n_trials`, non-normal return adjustment, selection-bias control. |
| PBO and CSCV | Bailey, Borwein, Lopez de Prado, Zhu, "The Probability of Backtest Overfitting", SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253 and PDF: https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf | `cscv_matrix`, `pbo_status`, `cscv_status`, probability of overfit across a configuration family. |
| PBO implementation checks | Bailey et al., "Mathematical Appendices to The Probability of Backtest Overfitting", SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2568435 | Test cases and accuracy checks for PBO/CSCV calculations. |
| Data snooping / bootstrap controls | White, "A Reality Check for Data Snooping", Econometrica 2000: https://www.ssc.wisc.edu/~bhansen/718/White2000.pdf | Justifies family-level correction and bootstrap-style data-snooping controls. |
| Technical trading data-snooping application | Sullivan, Timmermann, White, "Data-Snooping, Technical Trading Rule Performance, and the Bootstrap", Journal of Finance 1999: https://www.kevinsheppard.com/files/teaching/mfe/advanced-econometrics/Sullivan_Timmermann_White.pdf | Supports evaluating a full rule/configuration universe rather than a cherry-picked winner. |
| Walk Forward Correlation | Martyn Tinsley, "Walk Forward Correlation: A Diagnostic for Over-Fitting and Genuine Structural Edge", SSRN page: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6324079; explainer: https://www.interactivebrokers.com/campus/ibkr-quant-news/algo-advantage-053-martyn-tinsley-walk-forward-correlation-a-new-tool-for-robust-strategy-design/ | `wfc_rows`, `wfc_status`, full IS/OOS parameter-surface correlation, not final parameter selection. |
| VectorBT screening engine | VectorBT docs: https://vectorbt.dev/ and Portfolio docs: https://vectorbt.dev/api/portfolio/base/ | Fast vectorized parameter-grid screening and portfolio/stat surfaces before expensive replay. |
| HftBacktest realism engine | HftBacktest docs: https://hftbacktest.readthedocs.io/ and order fill docs: https://hftbacktest.readthedocs.io/en/latest/order_fill.html | Tick/order-book replay with feed/order latency, queue position, exchange/fill model, and market-impact limitations. |
| hft3 source hierarchy | `docs/project/ROBUSTNESS_TESTING_SPEC.md`, `docs/project/VECTORBT_SCREENING_ENGINE_SPEC.md`, `docs/project/HFTBACKTEST_REALISM_ENGINE_SPEC.md` | Local executable contract and fail-closed gate placement. |

## Readiness Checklist Before Next Testing

- Confirm the screening artifact has promoted rows and valid `screening_artifact_hash`.
- Confirm the screening artifact has complete measured event-by-parameter surfaces for the same model/symbol/event-type/research-clock/context-set family.
- Provide explicit fee-per-round-trip and tick-value stress inputs for the family being assembled.
- Build `hft3_robustness_raw_inputs_v1` with `scripts/build_robustness_raw_inputs_from_screening.py`.
- Build an apply-ready `hft3_robustness_evidence_inputs_v1` package with `scripts/package_robustness_evidence_inputs.py`.
- Run `scripts/apply_robustness_evidence_to_screening.py` on CHI404 or another permitted compute host, not MSI.
- Require at least one strict `replay_eligibility_status=eligible` row before HftBacktest/HFB manifest generation.
- Require `robustness_evidence_receipt` on any strict replay-eligible row; bridge-computed VectorBT metrics alone cannot certify HftBacktest replay eligibility.

## Known Non-Bypassable Gap

The repo currently has a raw-input assembler, a packager, an applicator, and producer libraries, but the current PR #13 smoke artifact does not contain complete family surfaces or explicit stress inputs that can produce packageable raw robustness entries. Until the VectorBT run emits complete measured event-by-parameter surfaces for at least one promoted family, the assembler produces `hft3_robustness_raw_inputs_v1` entries with WFC rows, CSCV matrices, per-event expectancy/stress arrays, p-values, surface-stability evidence, and source evidence pointers, and the packager adds/validates source SHA256 receipts, strict HftBacktest/HFB testing is blocked by design.
