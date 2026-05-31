# OpenFoundry integration (hft3)

hft3 vendors [syzygyhack/open-foundry](https://github.com/syzygyhack/open-foundry) under `vendor/openfoundry/` and extends it for CME MBO microstructure backtests.

Upstream is an **ontology platform** (ODL schema, domain packs, connectors). hft3 does not run the Open Foundry API server in the hot path — we use the vendor for schema/pack conventions and keep file-backed KG ingest in `data_layer/`.

## Connector

[`hft3-cme-mbo.yaml`](hft3-cme-mbo.yaml) maps workbench run artifacts under `research_cards/workbench_runs/` to hft3 after-action packets and JSONL KG nodes.

| Field | Value |
|-------|-------|
| `asset_class` | `cme_mbo_microstructure` |
| `upstream` | `https://github.com/syzygyhack/open-foundry` |
| Time discipline | Exchange timestamps in **ns**; latency chain in **µs** |

## Vendor pins

See [`VENDOR.lock`](VENDOR.lock). Initialize:

```bash
git submodule update --init vendor/openfoundry vendor/alphageometry
```

## hft3 adapter

`data_layer/openfoundry_bridge.py` validates the connector YAML and checks `vendor/openfoundry/domain-packs/core/pack.yaml` is present.

Quant X (`quant_data_refinery/`) is **not** used.

Vendor boundaries (OpenFoundry vs AlphaGeometry vs Ollama LLMs): [docs/research/VENDOR_BOUNDARIES.md](../../docs/research/VENDOR_BOUNDARIES.md).
