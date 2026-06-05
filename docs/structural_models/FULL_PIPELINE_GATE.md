# Full pipeline catalog gate

55-model rollup with **honest backend labels** and tiered runtime budgets.

## Tiers

| Tier | Budget | What runs |
|------|--------|-----------|
| `smoke` (default) | ~10–20 min | PDF_MODEL_4 hybrid (optional ablation skip), **one** `run_event_accurate_mbo` pass, fan-out **HYP_1 + HYP_5** only |
| `catalog` | ~2–4 hr | Full hybrid + ablation, fan-out **all 44 HYP**, PDF structural eval (6), diagnostics (3), options fixture (1) |

Non-executed models get manifest rows with `NOT_RUN_SMOKE` (smoke) or `NOT_RUN` (catalog).

## Commands

```bash
# CI / local quick loop
python scripts/run_full_pipeline_gate.py --tier smoke --event-id CPI_2024_09_11_TIGHT --symbol MES.v.0

# Nightly / manual full catalog
python scripts/run_full_pipeline_gate.py --tier catalog --event-id CPI_2024_09_11_TIGHT --symbol MES.v.0 --run-id FULL_CATALOG_20260605T000000Z

# Skip slow hybrid block (MBO fan-out only)
python scripts/run_full_pipeline_gate.py --tier smoke --event-id <EVENT_ID> --skip-hybrid --skip-unit-tests
```

## Outputs

| Path | Content |
|------|---------|
| `runtime/reports/full_pipeline_gate/<run_id>/manifest.json` | Gate manifest: `models[]` (always len 55), `validation_scope`, steps |
| `research_cards/pipeline_runs/<run_id>/report.md` | Master table with **backend honesty** column |
| `research_cards/pipeline_runs/<run_id>/result.json` | Machine-readable copy |
| `research_cards/pipeline_runs/<run_id>/{MODEL}_{event_id}/` | Per-model artifact dirs |

## Engine honesty

| `engine_kind` | Backend label in report |
|---------------|-------------------------|
| `hyp_mbo` | SignalBacktester MBO pipeline (research path) |
| `pdf_hybrid_replay` | ReplayRunner quote-engine (queue fills) |
| `pdf_structural_eval` | Structural signal eval (not queue-replay backtest) |
| `pdf_diagnostics` | Diagnostics-only (num_trades may be 0 by design) |
| `pdf_options_fixture` | Options parity fixture |

## Related

- PDF_MODEL_4-only gate: [`HYBRID_PIPELINE_GATE.md`](HYBRID_PIPELINE_GATE.md)
- Canonical research order: [`docs/vault/RESEARCH_ENTRYPOINTS.md`](../vault/RESEARCH_ENTRYPOINTS.md)

## Validation

```bash
pytest tests/backtest_pipeline/test_pipeline_model_router.py \
       tests/backtest_pipeline/test_pipeline_hyp_fanout.py \
       tests/backtest_pipeline/test_pipeline_gate_report.py -q
```

Full `catalog` tier is `@pytest.mark.slow` — not required in CI.
