# Research entrypoints (canonical order)

Use this order. Do **not** skip to legacy paths below.

**Verification honesty:** Every agent handoff must use the status block in [VALIDATION_HONESTY.md](../VALIDATION_HONESTY.md). Scope-green commands for each lane are in that doc; smoke-only targeted pytest never substitutes.

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

**Verify (scope-green):**

```bash
python -m pytest tests/test_run_event_replay.py tests/test_replay_must_emit_order_intents.py tests/test_replay_clock_order_timestamps.py -q
python scripts/run_hybrid_pipeline_gate.py --event-id CPI_2024_09_11_TIGHT  # when touching hybrid gate
```

## 1b. Autoresearch pipeline (NL thesis)

**When:** Ingest a natural-language trading thesis (and optional research PDF), generate candidate models, backtest, and write pipeline artifacts. Workstation-only; no live deploy until CHI404 is stable.

```bash
pip install -r packages/research_pipeline/requirements.txt

python scripts/run_pipeline.py \
  --thesis "Fade spread blowout after CPI surprise on MES" \
  --event-id CPI_2024_09_11_TIGHT \
  --max-candidates 5

# Parse + candidates only (no backtest)
python scripts/run_pipeline.py \
  --thesis "Fade spread blowout after CPI" \
  --event-id CPI_2024_09_11_TIGHT \
  --dry-run --no-llm
```

Optional document ingestion:

```bash
python scripts/run_pipeline.py \
  --thesis "..." \
  --doc docs/references/dev_instructions.pdf \
  --event-id CPI_2024_09_11_TIGHT
```

Output: `research_cards/pipeline_runs/<run_id>/`. Authority: [AUTORESEARCH_PIPELINE.md](../research/AUTORESEARCH_PIPELINE.md), source PDF [dev_instructions.pdf](../references/dev_instructions.pdf).

**Verify (scope-green):** `python -m pytest tests/test_research_pipeline.py -q`

## 1c. Low-float runner (equities lane)

**When:** Screen and backtest low-float momentum sessions on data-isolated equities data. Workstation-only; separate from CME MBO production path.

```bash
pip install -r packages/equities_lane/requirements.txt

python -m equities_lane.pipeline fixture-backtest

python -m equities_lane.pipeline experiment \
  --config packages/equities_lane/config/universe.yaml \
  --ablation all
```

Optional Databento download (requires `DATABENTO_API_KEY`):

```bash
python -m equities_lane.pipeline download --symbol GME --date 2021-01-27
python -m equities_lane.pipeline normalize --raw data/equities/raw/<file>.dbn.zst
```

Output: `research_cards/equities/<run_id>/`. Authority: [LOW_FLOAT_RUNNER.md](../research/LOW_FLOAT_RUNNER.md), source PDF [low_float_momentum_anomaly_research_pack.pdf](../references/low_float_momentum_anomaly_research_pack.pdf).

**Verify (scope-green):** `python -m pytest tests/test_equities_lane/ -q`

## 1d. Crypto alpha (crypto lane)

**When:** Walk-forward ML research on BTC spot/perp basis, funding, Deribit IV/RV, and local Bitcoin node mempool features. Workstation-only; quarantined from CME production path.

```bash
pip install -r packages/crypto_lane/requirements.txt

# One-time (or after hypothesis schema changes):
python packages/crypto_lane/scripts/generate_yaml_artifacts.py

python -m crypto_lane.pipeline discover
python -m crypto_lane.pipeline smoke --candidate crypto_h1_basis_compression
python -m crypto_lane.pipeline smoke
```

**Validation modes** (crypto addendum: [packages/crypto_lane/docs/VALIDATION_HONESTY.md](../../packages/crypto_lane/docs/VALIDATION_HONESTY.md); repo-wide: [VALIDATION_HONESTY.md](../VALIDATION_HONESTY.md)):

- **Dev/CI default:** `validation_mode: fixture` — bundled fixture CSVs; no live ingest required.
- **Production real-data:** run ingest first (`python -m crypto_lane.pipeline ingest` or pull/normalize steps), populate `data/crypto/normalized/`, then smoke with `validation_mode: production` in backtest YAML.

**Verify (scope-green gate):**

```bash
python -m pytest tests/test_crypto_lane/ -q
```

Targeted pytest on single files is smoke-only; it does not substitute for the command above.

Hypotheses: `research/hypotheses/crypto_alpha_engine_extracted_hypotheses.yaml`. Manifest: `research/hypotheses/crypto_alpha_engine_manifest.yaml`. Report: [crypto_alpha_engine_extraction_report.md](../../research/reports/crypto_alpha_engine_extraction_report.md).

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

**Verify (scope-green workstation):** `python -m pytest tests/test_rithmic_trial_pipeline.py tests/test_rithmic_topology_guards.py -q`. CHI404 PASS requires `validate_pass_criteria.py` on real logs — see [docs/chi404/VALIDATION_ADDENDUM.md](../chi404/VALIDATION_ADDENDUM.md).

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

**Verify (scope-green):** `python -m pytest tests/test_workbench/ -q`

**Latency authority:** C++ measured distributions from CHI404 probes — not Python wall time. See [docs/workbench/LATENCY_ARCHITECTURE.md](../workbench/LATENCY_ARCHITECTURE.md). (config, manifest, trades.parquet, report.md)
- Wraps `SignalBacktester` (primary) and documents HftBacktest queue path via matching config
- Does **not** replace `run_event_replay.py`; use workbench for per-model latency viability and promotion gates

### 6.1 Imbalance families (book / order-flow / auction)

**When:** Classify tape capability, compute institutional imbalance features, or run eight-mode ablation before promotion.

- Inventory: `python scripts/build_imbalance_inventory.py` → [docs/hft3_imbalance_inventory.md](../hft3_imbalance_inventory.md)
- Runbook: [docs/hft3_imbalance_runbook.md](../hft3_imbalance_runbook.md)
- Code: `packages/features_engine/src/imbalance/`
- Workbench artifacts: `workbench_runs/<run_id>/imbalance/`
- Ablation CLI: `python -m workbench imbalance-ablation --baseline-pnl 0.0`

**Verify:** `python -m pytest tests/test_imbalance/ -q`

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

**Verify (scope-green):** `python -m pytest tests/test_economic_event_universe/ -q`
