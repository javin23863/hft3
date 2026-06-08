# `hft3` — importable micro-package namespace

Consolidation layer over the `packages/` directory. Re-exports key
classes from legacy packages so new code can write:

```python
from hft3.pipelines.equities import LowFloatBacktester
from hft3.models.features import MBOEvent
```

**Not a refactor** — every import delegates to the original package.
Backward-compat shims remain until `pyproject.toml` ships the full layout.

| Sub-package | Backing package(s) | Status |
|-------------|-------------------|--------|
| `hft3.pipelines` | `equities_lane` (built), `crypto_lane`/`options_lane` (planned) | partial |
| `hft3.models` | `features_engine` (built), structural/hypotheses/regimes (planned) | partial |
| `hft3.connectors` | `data_system` (built), rithmic/exchange/data_layer (planned) | partial |
| `hft3.backtest` | `backtest_pipeline`, `replay`, `execution` | built |

The top-level `hft3/` is a PEP 420 namespace package; sub-packages
above are regular packages with `__init__.py`.

Validation and certification are available directly from `packages/hft3/`
(this dir is not shadowed by `src/hft3/validation/` because the legacy
package has priority in `sys.path`):

```python
from hft3.validation.certification_runner import run_full_certification
from hft3.validation.research_stamp import build_certification_stamp
```

See [docs/REPO_MAP.md](../../docs/REPO_MAP.md) for the full migration plan.
