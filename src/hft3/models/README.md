# `hft3.models` — features, structural models, hypotheses, regimes

**Built:** `features` — re-exports from `features_engine`.

**Planned (not yet migrated):** `structural` (execution models,
micro-price, entropy), `hypotheses` (candidate model registry bridge),
`regimes` (regime detection map).

These are thin re-exports from `features_engine` and `hfc3`.

```python
from hft3.models.features import MBOEvent, SpreadBlowoutRecompression
```

For structural models until the consolidator is built, import directly:

```python
from features_engine.src.structural_models import ...  # planned: hft3.models.structural
from hfc3.<module> import ...                           # planned: hft3.models.hypotheses
```
