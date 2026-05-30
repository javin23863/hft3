# Walk-forward campaigns

Authority: [`BLUEPRINT.md`](../../BLUEPRINT.md) §8, [`docs/REVIEWER_CHARTER.md`](../REVIEWER_CHARTER.md) B4, `chicago_cme_microstructure_a_plus_developer_handoff.pdf`, [`decision_engine/python/src/walk_forward.py`](../../decision_engine/python/src/walk_forward.py).

## Stages

| Stage | Years | Mode |
|-------|-------|------|
| Discovery | 2018–2020 | Tune allowed |
| Confirmation | 2021–2022 | Frozen params |
| Holdout | 2023–2024 | Evaluate-only |
| Recent holdout | **2025 only** | Evaluate-only |
| Sim shadow | 60 CME days from **2026-03-01** on CHI404 | Required before promote — see [SIM_SHADOW.md](SIM_SHADOW.md) |

Config: [`workbench/config/walk_forward.yaml`](../../workbench/config/walk_forward.yaml).

## Personal sandbox

2026-03-01 … 2026-05-30 local replay — excluded from promotion unless explicitly unlocked for personal runs only. See [PERSONAL_SANDBOX.md](PERSONAL_SANDBOX.md).

## Per-model events

Each model binds to event contexts from [`features_engine/src/hypotheses/modules.py`](../../features_engine/src/hypotheses/modules.py) via [`workbench/config/model_event_binding.yaml`](../../workbench/config/model_event_binding.yaml).

- HYP_29 → prop flatten windows (not CPI)
- HYP_5 (no gate) → macro `CPI_TIGHT` / `NFP_TIGHT` windows (NFP calendar in `release_calendars/bls_nfp.csv`)
- PDF_MODEL_5 → `options_lane` fixture MVP (quarantined under `research_cards/parity/`)

Regenerate binding:

```bash
python workbench/scripts/generate_model_event_binding.py
```

## Point-in-time data (not 10-year continuous MBO)

- Release dates: [`data_system/config/release_calendars/`](../../data_system/config/release_calendars/) (sourced `source_url`)
- Build `events.csv`: `python data_system/scripts/build_events_from_calendar.py`
- Campaign backfill: `python workbench/scripts/backfill_catalog.py --model HYP_5 --dry-run`
- Download missing: `python workbench/scripts/backfill_catalog.py --model HYP_5 --download-missing --max-cost-usd 25`

`min_history_years: 10` counts **distinct years with NPZ** for the model's bound events.

## CLI

```bash
python -m workbench campaign --model HYP_5 --symbol MES.v.0 --dry-run
python -m workbench campaign --model HYP_29 --symbol MES.v.0 --dry-run
python -m workbench campaign --model PDF_MODEL_5 --symbol MES.v.0 --dry-run
python -m workbench campaign --model HYP_5 --symbol MES.v.0 --enforce-history-gate --full-sweep --allow-partial
python -m workbench campaign --model HYP_5 --symbol MES.v.0 --dry-run \
  --defensive PDF_MODEL_3:continuous,PDF_MODEL_9:before,PDF_MODEL_11:during
python -m workbench campaign --campaign-id <id> --record-sim-shadow PASS
```

## Defensive composition (frozen at campaign start)

Primary alpha + optional defensive stubs run through `CompositionOrchestrator` on each event. Composition is written to `campaign.json` and **not re-tuned on holdout** — only the primary signal params use evaluate-only mode in Confirmation/Holdout; defensive veto thresholds stay frozen from Discovery.

See [MODEL_CATALOG.md](MODEL_CATALOG.md) for phases (`before` / `during` / `after` / `continuous`), budgets, and CLI `--composition` JSON.

Artifacts: `research_cards/workbench_runs/<campaign_id>/periods/<Stage>/events/<event_id>/` plus `composition_trace.json` per event when stubs are enabled.

## Walk Forward Correlation (WFC) gate

Before B4 period evaluation, campaigns with `parameter_bounds` in [`models.yaml`](../../workbench/config/models.yaml) run a **full parameter-matrix IS/OOS test** (config: [`wfc_gate.yaml`](../../workbench/config/wfc_gate.yaml)).

- WFC gates the **model family**, not final parameter selection (plateau selection runs only after WFC PASS).
- `wfc_status` must be `PASS` for `promote_candidate` in `summary.json`.
- Artifacts under `research_cards/workbench_runs/<campaign_id>/wfc/`: `param_matrix.parquet`, `wfc_summary.json`, scatter plots, `wfc_audit.log`.
- Tune strategy hyperparameters on Discovery only; Holdout evaluation never influences the matrix.

## UI

Streamlit Model Selector → lane filter, model metadata, Start/Pause/Stop, personal lock (sidebar), Download missing. Drill down campaign → period → event; **Personal Runs** tab when unlocked.
