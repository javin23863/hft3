# Institutional Model Metrics Contract

This repo now has an additive, asset-class-neutral measurement layer for
post-robustness model scorecards, behavior envelopes, and external/shadow
state checks.

The reusable engine lives in `packages/model_metrics`. Validation code can use
`packages/hft3/validation/model_metrics.py`; Trade Manager code can use
`packages/trade_manager/model_behavior.py`.

The layer does not decide promotion by itself. It records evidence, grades the
model, creates an operating envelope, and lets existing promotion/risk gates use
those artifacts.

Artifacts:

- `model_metric_values.json`
- `model_scorecard.json`
- `model_behavior_envelope.json`
- `model_metric_calculation_logs.json`
- `model_state_history.jsonl` and `model_alerts.jsonl` are the live monitoring
  append-only targets for future session integration.

Missing inputs are not silently converted into zeros. The metric value is
`null`, `status=unavailable`, and the metric carries an explicit warning/error
reason.

Run backfill:

```powershell
$env:PYTHONPATH='.;packages;apps'
python scripts/backfill_model_metrics.py --root .
```
