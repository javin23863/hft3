# After-action reports (post-run LLM layer)

Post-run only on the MSI workstation — never in the MBO replay hot path or on CHI404 (BLUEPRINT §4).

## Trigger

After a full-fidelity workbench run (`--full-sweep`, not `fast_sweep`), `WorkbenchEngine.run()` calls after-action when `_after_action_allowed()` is true:

```python
if not fast_sweep and _after_action_allowed():
    run_after_action_report(artifact_dir, repo_root)
```

### Host guard

| Platform | Default |
|----------|---------|
| Windows / macOS (dev workstation) | **enabled** |
| Linux (CHI404) | **disabled** |

Override with environment variables:

- `HFT3_AFTER_ACTION=1` — force enable (e.g. Linux dev box)
- `HFT3_AFTER_ACTION=0` — force disable
- `HFT3_LLM_TIMEOUT_S=600` — LLM generation timeout (default 600)

Do not set `HFT3_AFTER_ACTION=1` on CHI404 production hosts.

## Setup

1. Vendor submodules: `vendor/openfoundry/` ([syzygyhack/open-foundry](https://github.com/syzygyhack/open-foundry)), `vendor/alphageometry/` — pins in `integrations/openfoundry/VENDOR.lock`
2. OpenAI-compatible GPT-5.5 endpoint: set `HFT3_LLM_API_KEY` or `OPENAI_API_KEY`; override model with `HFT3_AAR_LLM_MODEL`
3. PDF bundle in `docs/references/` — see [MANIFEST.md](../references/MANIFEST.md)

## Time discipline

| Field class | Unit |
|-------------|------|
| Exchange / fill timestamps | **ns** |
| Latency chain, injection sweep | **µs** |
| `python_research_runtime_us` | µs, **non-authoritative** |

## Per-run artifacts

| File | Content |
|------|---------|
| `after_action_packet.json` | `MicrostructureAARPacket` |
| `after_action_symbolic.json` | Invariant pass/fail |
| `after_action_response.json` | Canonical LLM output (`schema_aar_response_v1`) |
| `after_action_report.md` | Derived human view (`narrative_md`) |
| `after_action_annotations.json` | KG edge proposals (legacy compat) |
| `after_action_meta.json` | skip_reason, llm_status, vendor SHAs |
| `kg_slice.json` | Portable subgraph for this run |

Global KG append: `research_cards/kg/nodes.jsonl`, `edges.jsonl`.

## Skip rules

| Condition | `skip_reason` |
|-----------|---------------|
| `data_sufficient == false` | `HISTORY_GATE` |
| trades > 0 but incomplete audit | `AUDIT_INCOMPLETE` |
| trades > 0, no `trades.parquet`, `execution_assumptions == quote_engine` | LLM allowed; packet `audit_waiver_reason: quote_engine_aggregate_only` (discovery hybrid gate only; not production promotion) |
| GPT-5.5 endpoint unavailable or unconfigured | `LLM_UNAVAILABLE` |
| Required PDFs missing | LLM skipped (`pdf_citations_complete: false`) |

Symbolic checks and KG ingest still run when LLM is skipped.

## CLI example

```bash
python -m workbench run --model HYP_5 --event-id CPI_2024_09_11_TIGHT \
  --chi404-summary runtime/latency_reports/latency_summary.json \
  --full-sweep
```

## UI

Streamlit **Report** tab shows `after_action_report.md` plus chips for `lane_pass`, `breakeven_us`, and symbolic pass/fail.

## Tests

```bash
pytest tests/test_data_layer/ -q
```
