# Research entrypoints (canonical order)

Use this order. Do **not** skip to legacy paths below.

Baseline metrics: [CPI_2024_09_11_TIGHT_BASELINE.md](CPI_2024_09_11_TIGHT_BASELINE.md)

## 1. Macro event replay (primary research)

**Backtester certification:** Every replay output includes a `certification_stamp` (T1). See [BACKTESTER_CERTIFICATION.md](BACKTESTER_CERTIFICATION.md) for T0–T4 gates.

**When:** Backtest a scheduled macro window (CPI, NFP, etc.) on Databento MBO with CHI404-measured latency.

```bash
python scripts/run_event_replay.py \
  --event-id CPI_2024_09_11_TIGHT \
  --chi404-summary runtime/latency_reports/latency_summary.json
```

- Resolves window from `data_system/config/events.csv` (never `date +%F` as research key).
- **Primary engine:** `replay_execution_adapter` (`ReplaySession` + `HftBacktestSimulatedExchangeAdapter` + combined hypotheses).
- **Secondary engine:** `per_hypothesis_replay` (same adapter contract, one hypothesis per session).
- Use `--skip-combined-replay` to run per-hypothesis matrix only (faster).
- Output: `research_cards/<event_id>_replay/`
- Lifecycle audits: `runtime/replay_audits/{run_id}_order_lifecycle.jsonl`

Equivalent:

```bash
python -m data_system.rithmic_trial.pipeline replay-event \
  --event-id CPI_2024_09_11_TIGHT
```

### PDF_MODEL_4 hybrid (structural stack + queue fills)

**When:** Trial Avellaneda–Stoikov hybrid execution with PDF_MODEL_1 OFI + PDF_MODEL_3 VPIN on the same Databento NPZ.

```bash
python scripts/run_pdf_hybrid_replay.py --event-id CPI_2024_09_11_TIGHT
```

Or via event replay:

```bash
python scripts/run_event_replay.py --event-id CPI_2024_09_11_TIGHT --engine pdf_hybrid
```

See [docs/structural_models/PDF_HYBRID_REPLAY.md](../structural_models/PDF_HYBRID_REPLAY.md). Output: `research_cards/PDF_MODEL_4_hybrid_replay/`.

Ablation matrix (all four defensive modes):

```bash
python scripts/run_pdf_hybrid_ablation.py --event-id CPI_2024_09_11_TIGHT
```

Output: `research_cards/PDF_MODEL_4_defensive_ablation/`.

**Gate (ablation + hybrid replay + after-action report):**

```bash
python scripts/run_hybrid_pipeline_gate.py --event-id CPI_2024_09_11_TIGHT
```

See [docs/structural_models/HYBRID_PIPELINE_GATE.md](../structural_models/HYBRID_PIPELINE_GATE.md). Outputs: `research_cards/PDF_MODEL_4_hybrid_replay/`, `research_cards/PDF_MODEL_4_defensive_ablation/`, `research_cards/PDF_MODEL_4_hybrid_pipeline/` (AAR), `runtime/reports/hybrid_pipeline_gate.json`.

**Full 55-model catalog gate:**

```bash
python scripts/run_full_pipeline_gate.py --tier smoke --event-id CPI_2024_09_11_TIGHT --symbol MES.v.0
python scripts/run_full_pipeline_gate.py --tier catalog --event-id CPI_2024_09_11_TIGHT --symbol MES.v.0
```

Or via hybrid wrapper: `python scripts/run_hybrid_pipeline_gate.py --tier smoke`. See [docs/structural_models/FULL_PIPELINE_GATE.md](../structural_models/FULL_PIPELINE_GATE.md).

## 2. Single-hypothesis drill-down

**When:** One hypothesis family on the same event NPZ.

```bash
python scripts/run_single_hyp_backtest.py \
  --event-id CPI_2024_09_11_TIGHT \
  --chi404-summary runtime/latency_reports/latency_summary.json
```

Uses `SignalBacktester` only (not `ReplayRunner`).

## 3. Full hypothesis matrix (offline sweep)

**When:** Latency band sweep across all active hypotheses.

```bash
python backtest_pipeline/src/research_runner.py \
  --data data/npz/MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz \
  --skip-hft
```

Or orchestrated:

```bash
python scripts/run_offline_pipeline.py --skip-download --event-id CPI_2024_09_11_TIGHT
```

Uses `SignalBacktester` per hypothesis. HftBacktest combined replay is **opt-in** (`--full-hft` on offline pipeline).

## 4. Rithmic trial live (CHI404 only)

**When:** Paper-forward capture + optional replay after live session.

```bash
# On CHI404 — tag manifest with macro event_id; replay uses Databento NPZ + CHI404 latency
EVENT_ID=CPI_2024_09_11_TIGHT bash scripts/chi404_run_trial_live.sh
```

- `--date` / folder `YYYY-MM-DD` = **capture session date** (ingest only).
- `EVENT_ID` / `replay-event` = **research event** (from `events.csv`).

**Paper order latency (≥1,000 real pairs):** [CHI404_CANONICAL_ENTRYPOINTS.md](CHI404_CANONICAL_ENTRYPOINTS.md) — `chi404_vm_deploy.sh` then `chi404_run_paper_latency_sweep.sh`. No synthetic log inject.

## 5. Legacy / smoke only (do not use for macro research)

| Path | Role | Why not for CPI research |
|------|------|---------------------------|
| `pipeline replay-sample --simple` | Trial NPZ smoke | Trade-only trial capture; wrong calendar; no event_id |
| `ReplayRunner` alone | Queue fill experiment | Was depth-only + mean@0.25 before fix; use via `run_event_replay` |
| `run_offline_pipeline` without `--event-id` | Old matrix-only | Skips canonical event replay report |
| Trial NPZ under `data/replay/hftbacktest/rithmic_trial/` | Infra quarantine | Not Databento CPI body |

Smoke / CI:

```bash
bash scripts/chi404_run_trial_smoke.sh   # fixture export only
python -m pytest tests/test_rithmic_trial_pipeline.py -q
```

## Data sources (do not mix)

| Layer | Source | Path |
|-------|--------|------|
| Macro MBO replay body | Databento | `data/npz/<symbol>_<event_id>_mbo.npz` |
| Colo latency | CHI404 probe | `runtime/latency_reports/latency_summary.json` |
| Rithmic live tape | CHI404 capture | `data/raw/rithmic_trial_live_capture/` (forward-only) |

No historical Rithmic download for past CPI dates.

## PDF structural models (signal layer, not HYP backtest wiring)

Seven models from [algorithmic_trading_strategy_development.pdf](../references/algorithmic_trading_strategy_development.pdf) are separate from the 44 `HYP_*` families:

- Code: `features_engine/src/structural_models/`
- Specs: [docs/structural_models/PDF_MODELS.md](../structural_models/PDF_MODELS.md)
- Registry: `get_structural_models()` (not `get_active_hypotheses()`)

Macro event replay (`run_event_replay.py`) runs HYP backtests by default; PDF hybrid replay is available via `--engine pdf_hybrid` or [PDF_HYBRID_REPLAY.md](../structural_models/PDF_HYBRID_REPLAY.md).

## 6. Microstructure workbench (unified 51-model research)

**When:** Latency viability, break-even analysis, robustness pack, and audit artifacts for any `HYP_*` or `PDF_MODEL_*`.

```bash
python -m workbench run \
  --model HYP_5 \
  --event-id CPI_2024_09_11_TIGHT \
  --chi404-summary runtime/latency_reports/latency_summary.json
```

```bash
streamlit run workbench/ui/app.py
```

**Desktop shortcut (Windows):** run once `powershell -File scripts/create_workbench_desktop_shortcut.ps1`, then double-click **HFT3 Workbench** on the desktop. Grader audit playbook: [docs/workbench/GRADER_CHECKLIST.md](../workbench/GRADER_CHECKLIST.md). Walk-forward campaigns: [docs/workbench/WALK_FORWARD_CAMPAIGNS.md](../workbench/WALK_FORWARD_CAMPAIGNS.md).

```bash
python -m workbench campaign --model HYP_5 --symbol MES.v.0 --dry-run
```

- Unified registry: `workbench/config/models.yaml` + `workbench/src/registry/unified_registry.py` (44 HYP + 7 PDF)
- Artifacts: `research_cards/workbench_runs/<run_id>/`

**Latency authority:** C++ measured distributions from CHI404 probes — not Python wall time. See [docs/workbench/LATENCY_ARCHITECTURE.md](../workbench/LATENCY_ARCHITECTURE.md). (config, manifest, trades.parquet, report.md)
- Wraps `SignalBacktester` (primary) and documents HftBacktest queue path via matching config
- Does **not** replace `run_event_replay.py`; use workbench for per-model latency viability and promotion gates

## 7. Economic event universe (macro calendar API)

**When:** Query upcoming releases, user-timezone display, rebuild `events.csv`, or offline cross-asset L3 tensors.

Full guide: [ECONOMIC_EVENT_UNIVERSE.md](ECONOMIC_EVENT_UNIVERSE.md)

```bash
PYTHONPATH=packages python -m economic_event_universe.cli validate
python packages/data_system/scripts/build_events_from_calendar.py --dry-run
python scripts/build_event_cross_asset_snapshot.py --event-id CPI_2024_09_11_TIGHT
```

```python
from economic_event_universe import list_upcoming
for ev in list_upcoming("Asia/Phnom_Penh")[:3]:
    print(ev.event_id, ev.anchor_user_tz, ev.source_url)
```
