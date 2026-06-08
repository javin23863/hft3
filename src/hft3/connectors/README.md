# `hft3.connectors` — data connectors

**Built:** `databento` — re-exports from `data_system`.

**Planned (not yet migrated):** `rithmic` (Rithmic MBO bridge),
`exchange` (crypto exchange connectors), `data_layer` (lake source
resolution).

```python
from hft3.connectors.databento import resolve_npz_for_event, DatabentoResearchClient
```

For other connectors until the consolidator is built, import directly:

```python
from data_system.rithmic_trial.<module> import ...   # planned: hft3.connectors.rithmic
from data_layer.src.<module> import ...               # planned: hft3.connectors.data_layer
```
