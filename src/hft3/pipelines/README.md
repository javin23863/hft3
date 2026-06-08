# `hft3.pipelines` — lane research pipelines

**Built:** `equities` — re-exports from `equities_lane`.

**Planned (not yet migrated):** `crypto` → `crypto_lane`,
`options` → `options_lane`, `futures` → `data_system` rithmic trial.

Usage:

```python
from hft3.pipelines.equities import LowFloatBacktester, load_universe
bt = LowFloatBacktester(universe=load_universe(...))
```

For other lanes, import directly from the backing package until the
consolidator is built:

```python
from crypto_lane.src.<module> import ...   # planned: hft3.pipelines.crypto
from options_lane.src.<module> import ...  # planned: hft3.pipelines.options
```
