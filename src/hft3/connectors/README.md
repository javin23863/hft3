# `hft3.connectors` — data connectors

| Module | Key exports |
|--------|-------------|
| `databento` | `resolve_npz`, `EventDataResolver`, `DatabentoClient`, `DatabentoFeatureClient`, `DatabentoL2Gateway` |
| `rithmic` | _(planned — Rithmic MBO bridge)_ |
| `exchange` | _(planned — crypto exchange connectors)_ |
| `data_layer` | _(planned — lake source resolution)_ |

```python
from hft3.connectors.databento import EventDataResolver
resolver = EventDataResolver(data_root=...)
```
