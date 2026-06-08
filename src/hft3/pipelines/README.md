# `hft3.pipelines` — lane research pipelines

| Module | Backing package | Key exports |
|--------|----------------|-------------|
| `equities` | `equities_lane` | `LowFloatBacktester`, `load_universe`, `run_feature_pipeline` |
| `crypto` | `crypto_lane` | _(planned)_ |
| `options` | `options_lane` | _(planned)_ |
| `futures` | `(data_system)` | _(planned)_ |

Usage:

```python
from hft3.pipelines.equities import LowFloatBacktester
bt = LowFloatBacktester(universe=load_universe())
```

Config: see `configs/equities/` and `configs/crypto/`.
