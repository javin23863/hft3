# `hft3.backtest` — backtest runners, certification, walk-forward

| Module | Key exports |
|--------|-------------|
| `pipeline` | `BacktestRunner`, `ReplayMatrix`, `PdfHybridStrategy`, `EcbCutter`, `MultipathBacktest`, `SinglePathBacktest`, ... |
| `certification` | `CertificationRunner`, `BenchmarkSweep`, `FastGateReport` |

Backed by `backtest_pipeline`, `replay`, and `execution`.

```python
from hft3.backtest.pipeline import BacktestRunner
runner = BacktestRunner(strategy="SPREAD_BLOWOUT_RECOMPRESSION")
```

Fast gate: `pytest tests/backtester_validation/fast -q`.
