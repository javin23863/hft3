# Centralised configuration

Canonical source for YAML/JSON configs, copied from lane-specific
`packages/*/config/` directories. Originals remain for backward compat.

**Do not edit config files under `packages/`** — edit here and sync back
until the loader code is updated to read from `configs/` directly.

| Sub-directory | Contents |
|---------------|----------|
| `equities/` | `universe.yaml`, `decadal_runners.yaml` |
| `crypto/` | `universe.yaml`, `lake_sources.yaml` |
| `options/` | `parity_universe.yaml` |
| `futures/` | `rithmic_trial.yaml` |
| `features/` | `model_registry.yaml`, `pdf_model_params.yaml`, `imbalance_features.yaml` |
| root | `event_universe.yaml` (economic events) |

Loading convention (to be adopted):

```python
import hft3_bootstrap
hft3_bootstrap.setup_repo_paths()
# then: open("configs/equities/universe.yaml")
```
