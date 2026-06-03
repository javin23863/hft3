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

## hft3 domain pack

The hft3 ODL pack lives under [`domain-packs/hft3/`](domain-packs/hft3/):

| Path | Role |
|------|------|
| `pack.yaml` | Declares the hft3 pack and 9 schema files |
| `schema/*.odl` | ODL object types declared by `hft3-cme-mbo.yaml` |
| `citations/*.yaml` | Sidecar PDF citations for each ODL object type |

Inline `@pdf_cite(...)` directives are not used because the vendor ODL parser silently drops unknown directives. Sidecar YAML is the citation source of truth.

## hft3 adapter

`data_layer/openfoundry_bridge.py` validates the connector YAML, checks `vendor/openfoundry/domain-packs/core/pack.yaml` is present, and enforces hft3 sidecar citations with `validate_ontology_citations()`.

Failure status mapping in `data_layer.llm.packet_runner`:

| llm_status | Trigger |
|------------|---------|
| `skipped_uncited_ontology` | Missing/malformed ODL sidecar or empty claims |
| `skipped_broken_pdf_cite` | Sidecar cites a missing PDF |
| `skipped_connector` | Generic vendor/connector failure |

Quant X (`quant_data_refinery/`) is **not** used.

Vendor boundaries (OpenFoundry vs AlphaGeometry vs GPT-5.5 runtime): [docs/research/VENDOR_BOUNDARIES.md](../../docs/research/VENDOR_BOUNDARIES.md).

Ontology hardening details: [docs/research/ONTOLOGY_HARDENING.md](../../docs/research/ONTOLOGY_HARDENING.md) · [docs/research/ONTOLOGY_CITATIONS.md](../../docs/research/ONTOLOGY_CITATIONS.md).
