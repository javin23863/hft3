# `hft3` — importable micro-package namespace

Consolidation layer over the `packages/` directory. Re-exports key
classes from legacy packages so new code can write:

```python
from hft3.pipelines.equities import LowFloatBacktester
from hft3.models.features import MBOEvent
```

**Not a refactor** — every import delegates to the original package.
Backward-compat shims remain until `pyproject.toml` ships the full layout.

| Sub-package | Backing package(s) |
|-------------|-------------------|
| `hft3.pipelines` | `equities_lane`, `crypto_lane`, `options_lane` |
| `hft3.models` | `features_engine`, `hfc3` |
| `hft3.connectors` | `data_system`, `data_layer` |
| `hft3.backtest` | `backtest_pipeline`, `replay`, `execution` |
| `hft3.data` | `data_system` (data resolution / NPZ) |

Validation and certification are available directly from `packages/hft3/`:
```python
from hft3.validation.certification_runner import run_full_certification
from hft3.validation.research_stamp import build_certification_stamp
```

See [docs/REPO_MAP.md](../../docs/REPO_MAP.md) for the full migration plan.
