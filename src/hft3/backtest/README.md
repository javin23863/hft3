# `hft3.backtest` — backtest runners, certification, walk-forward

**Built:** `pipeline` — re-exports from `backtest_pipeline`.

Backed by `backtest_pipeline`, `replay`, and `execution`.

```python
from hft3.backtest.pipeline import ReplayRunner, run_all_hypotheses_replay
runner = ReplayRunner(strategy="SPREAD_BLOWOUT_RECOMPRESSION")
```

Certification and gates are reached through the legacy
`hft3.validation.*` namespace from `packages/hft3/`:

```python
from hft3.validation.certification_runner import run_full_certification
from hft3.validation.promotion_gate import evaluate_promotion_gate
```

Fast gate: `pytest tests/backtester_validation/fast -q`.
