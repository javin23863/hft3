# Workbench grader checklist

Manual audit playbook for reviewing workbench runs — not invoked by the desktop shortcut.

## A. Run a backtest (CLI — full fidelity flags)

Single event:

```bash
python -m workbench run --model HYP_5 --event-id CPI_2024_09_11_TIGHT \
  --chi404-summary runtime/latency_reports/latency_summary.json \
  --full-sweep --enforce-history-gate
```

Walk-forward campaign (B4 stages, per-model events):

```bash
python -m workbench campaign --model HYP_5 --symbol MES.v.0 \
  --chi404-summary runtime/latency_reports/latency_summary.json \
  --full-sweep --enforce-history-gate --allow-partial
python -m workbench campaign --model HYP_5 --symbol MES.v.0 --dry-run \
  --defensive PDF_MODEL_9:before:50,PDF_MODEL_11:during
python -m workbench campaign --model PDF_MODEL_5 --dry-run
python -m workbench campaign --campaign-id <id> --record-sim-shadow PASS
```

Composed stack (JSON file):

```bash
python -m workbench campaign --model HYP_5 --composition stack.json --allow-partial
```

See [MODEL_CATALOG.md](MODEL_CATALOG.md), [WALK_FORWARD_CAMPAIGNS.md](WALK_FORWARD_CAMPAIGNS.md), [SIM_SHADOW.md](SIM_SHADOW.md), [PERSONAL_SANDBOX.md](PERSONAL_SANDBOX.md).

## B. Inspect artifacts

Single run: `research_cards/workbench_runs/<run_id>/`

Campaign: `research_cards/workbench_runs/<campaign_id>/periods/<Stage>/events/<event_id>/`

| File | Check |
|------|-------|
| `manifest.json` | gap count, `data_sufficient` |
| `diagnostics.json` | `survives_cpp_execution_delay`, `cpp_hot_path_runtime_us`, `python_research_runtime_us`, `promote_candidate` |
| `trades.parquet` | timestamp chain + µs fields per [LATENCY_ARCHITECTURE.md](LATENCY_ARCHITECTURE.md) |
| `report.md` | narrative cites C++ latency authority |
| `composition_trace.json` | per-phase stub budgets, veto counts, raw vs adjusted signal (composed campaigns) |
| `campaign.json` | frozen `composition` + `phase_budgets_us` at B4 start |

## C. PDF / math cross-reference

| Model | Spec | Tests |
|-------|------|-------|
| PDF_MODEL_1..7 | [PDF_MODELS.md](../structural_models/PDF_MODELS.md) + [algorithmic_trading_strategy_development.pdf](../references/algorithmic_trading_strategy_development.pdf) | `tests/structural_models/test_model_*.py` |
| HYP_1..44 | [BLUEPRINT.md](../../BLUEPRINT.md) §7–8 | `tests/test_run_event_replay.py` |

## D. Verify commands (run when grading)

```bash
pytest tests/test_workbench/ tests/structural_models/ -q
pytest tests/test_workbench/test_cpi_e2e.py -q   # requires CPI NPZ locally
```

Or on Windows:

```powershell
powershell -File scripts/verify_workbench.ps1
```

## E. Promotion gate (hard fail if any missing)

- `survives_cpp_execution_delay == true`
- `simulated_latency_adjusted_pnl > 0`
- `--full-sweep` used (not fast heuristic)
- `--enforce-history-gate` passed
- Robustness not stub-passed on fake trade PnLs

## F. Known engine gaps

- Default CLI still allows `fast_sweep` / `skip_history_gate` — grader must use full flags above
- PDF non-diagnostic models use stub PnL — grade via structural tests + PDF formulas, not Promote Candidate alone
