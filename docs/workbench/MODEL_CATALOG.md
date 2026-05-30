# Model catalog and defensive composition

Authority: [hft_framework_developer_prompt.pdf](../../hft_framework_developer_prompt.pdf), [LATENCY_ARCHITECTURE.md](LATENCY_ARCHITECTURE.md), [WALK_FORWARD_CAMPAIGNS.md](WALK_FORWARD_CAMPAIGNS.md).

## Catalog

Each of the 55 workbench models has metadata in [workbench/config/model_catalog.yaml](../../workbench/config/model_catalog.yaml):

| Field | Purpose |
|-------|---------|
| `display_name` | Human title in dashboard |
| `description` | 1–2 sentence summary (small font in UI) |
| `role` | `alpha`, `defensive`, or `hybrid` |
| `default_phase` | `before`, `during`, `after`, `continuous` |
| `budget_us` | Default compute budget for orchestrator |
| `blocks_trade` | Pre-send veto when defense fires |
| `requires` | Auto-included dependencies (e.g. PDF_MODEL_11 → PDF_MODEL_4) |

Loader: `workbench/src/registry/model_catalog.py`.

## Phased decision pipeline

| Phase | When | Example stubs |
|-------|------|---------------|
| `continuous` | Every book/VPIN bar | PDF_MODEL_3 VPIN |
| `before` | Pre-send gate | PDF_MODEL_9 quantum cancel |
| `during` | Quote/reservation adjust | PDF_MODEL_4 + PDF_MODEL_11 Hawkes γ skew |
| `after` | Post-fill / slice end | HYP_29 flatten |

Decision-path budget ≈ **before + during stub budgets + C++ p99** (from CHI404 profile).

## CLI

```bash
python -m workbench run --model HYP_5 --event-id CPI_2024_09_11_TIGHT \
  --defensive PDF_MODEL_9:before:50,PDF_MODEL_11:during

python -m workbench campaign --model HYP_5 --symbol MES.v.0 --dry-run \
  --defensive PDF_MODEL_3:continuous,PDF_MODEL_9:before
```

Or JSON composition file:

```json
{
  "primary_model_id": "HYP_5",
  "defensive_stubs": [
    {"model_id": "PDF_MODEL_9", "phase": "before", "budget_us": 50, "enabled": true}
  ]
}
```

```bash
python -m workbench campaign --model HYP_5 --composition stack.json --allow-partial
```

## Campaign artifacts

- `campaign.json` — frozen composition at campaign start (B4)
- `composition_trace.json` — per-event phase steps, veto counts, signal raw/adjusted
- `summary.json` — `phase_budgets_us`, `trades_vetoed_by_defense`

## UI

Streamlit **Model Selector** tab: alpha/defensive catalogs with descriptions, **Stack builder** with phase/budget controls and timing summary. Start Campaign passes composition to subprocess.
