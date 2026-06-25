# Edge Testing And Alpha Evaluation Plan

Source: Codex attachment `b888946d-3b67-4e42-b7d5-d78c73dd852f/pasted-text.txt`

Status: mandatory implementation plan for the hft3 autoresearch pipeline.

## Goal

Make hft3 alpha evaluation more robust by reducing false discoveries,
selection bias, overfitting, and overstated gross performance. The pipeline
must record enough evidence for a future reviewer to understand why a candidate
passed or failed.

## Required Work

1. Add statistical significance and multiple-testing controls.
   - Create `packages/research_pipeline/statistics.py`.
   - Implement probabilistic Sharpe ratio, deflated Sharpe ratio, adjusted
     p-values, and minimum track record length.
   - Extend `GateThresholds` with `min_psr` and `min_dsr`.
   - Extend `EvaluationResult` with PSR and DSR outputs.
   - Track number of trials and Sharpe variance across candidates.
   - Add CLI/config overrides and documentation.

2. Add combinatorially symmetric cross-validation and rolling validation.
   - Create `packages/research_pipeline/cross_validation.py`.
   - Compute probability of backtest overfitting, performance degradation, and
     probability of loss from a candidate performance matrix.
   - Add rolling/expanding window validation helpers.
   - Add `--cscv`, `--cscv-subsets`, `--rolling-validation`,
     `--rolling-window`, and `--rolling-step`.
   - Extend gates with `max_pbo`.

3. Add power analysis and sample-size checks.
   - Create `packages/research_pipeline/power_analysis.py`.
   - Implement minimum sample size and Sharpe effect size helpers.
   - Record whether each candidate has enough observations for the configured
     alpha and power assumptions.

4. Model transaction costs and slippage.
   - Create `packages/research_pipeline/cost_model.py`.
   - Estimate spread, commission, slippage, and market impact costs.
   - Store gross PnL, net PnL, total cost, and cost breakdown.
   - Add config and CLI overrides for core cost assumptions.

5. Add risk and distribution metrics.
   - Compute CVaR at 95 and 99 percent, tail ratio, skewness, kurtosis,
     turnover, and average trade duration when data is available.
   - Extend gates for CVaR and tail-ratio thresholds.

6. Track trial count and selection bias.
   - Count generated and evaluated candidates.
   - Write `num_trials.json` for each artifact-producing run.
   - Include trial count and Sharpe variance in evaluation metadata.

7. Expand regime and instrument testing.
   - Provide helpers to label observations by regime and aggregate performance
     by regime/instrument.
   - Honor valid instrument-universe metadata before cross-instrument tests.
   - Record per-regime and per-instrument metrics.

## Acceptance Notes

- No plaintext secrets.
- Do not use `C:\Users\MSI\Documents\hft3` as an active repo.
- Canonical working tree is `C:\Users\MSI\repos\hft3`.
- Existing permissive defaults may remain permissive, but every new metric must
  be computable, recordable, and configurable as a gate.
