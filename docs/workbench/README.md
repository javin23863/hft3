# Workbench lane

Microstructure backtests on MBO NPZ event windows with **C++-measured latency authority**, walk-forward campaigns, and optional **post-run after-action reports**.

Read [GETTING_STARTED.md §7–8](../GETTING_STARTED.md) first for setup order.

## Reading order (this lane)

| Order | Doc | Content |
|-------|-----|---------|
| 1 | [LATENCY_ARCHITECTURE.md](LATENCY_ARCHITECTURE.md) | µs C++ hot path vs Python research runtime |
| 2 | [MODEL_CATALOG.md](MODEL_CATALOG.md) | `HYP_*` and `PDF_MODEL_*` registry |
| 3 | [WALK_FORWARD_CAMPAIGNS.md](WALK_FORWARD_CAMPAIGNS.md) | B4 periods, holdout discipline |
| 4 | [GRADER_CHECKLIST.md](GRADER_CHECKLIST.md) | PASS artifacts and pytest gates |
| 5 | [AFTER_ACTION_REPORTS.md](AFTER_ACTION_REPORTS.md) | Post-run packet, symbolic pass, GPT-5.5 |

Optional: [SIM_SHADOW.md](SIM_SHADOW.md), [PERSONAL_SANDBOX.md](PERSONAL_SANDBOX.md).

## CLI entrypoints

```bash
python -m workbench list
python -m workbench run --model HYP_5 --event-id CPI_2024_09_11_TIGHT --full-sweep
python -m workbench campaign --model HYP_5 --symbol MES.v.0 --full-sweep
python -m workbench ui   # Streamlit Report tab shows after-action when present
```

Artifacts: `research_cards/workbench_runs/` (local, gitignored). Campaign copies land under `periods/.../events/`.

## Code map

| Component | Path |
|-----------|------|
| Run orchestrator | `workbench/src/run/engine.py` |
| Campaigns | `workbench/src/run/campaign_runner.py` |
| Trade audit (ns / µs) | `workbench/src/core/trade_audit.py` |
| Latency viability | `workbench/src/latency/viability.py` |
| After-action pipeline | `data_layer/pipeline/after_action.py` |
| Open Foundry connector | `integrations/openfoundry/hft3-cme-mbo.yaml` |

## Tests

```bash
pytest tests/test_workbench/ tests/test_data_layer/ -q
pytest tests/test_data_layer/ -q -m "not slow"   # skip live GPT-5.5 endpoint test
```
