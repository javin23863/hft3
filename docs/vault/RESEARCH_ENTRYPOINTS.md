# Research entrypoints (canonical order)

Use the active HBT-only order for current HftBacktest-only backtesting work. Do
**not** skip to legacy paths below.

**Active HBT-only supersession (2026-06-29):** for current HftBacktest-only
backtesting work, use
[HFTBACKTEST_ONLY_PIPELINE_PLAN.md](../project/HFTBACKTEST_ONLY_PIPELINE_PLAN.md)
and
[HFTBACKTEST_ONLY_EVIDENCE_PARAMETER_SURFACE_PLAN.md](../project/HFTBACKTEST_ONLY_EVIDENCE_PARAMETER_SURFACE_PLAN.md).
VectorBT, Stage A, screening artifacts, and robustness bridges are historical or
diagnostic inputs only; they do not decide what active HBT receives.

**Legacy chronological pipeline:** [UNIFIED_RESEARCH_PIPELINE.md](UNIFIED_RESEARCH_PIPELINE.md) — stages 0–7 (ontology → VBT → promote → HBT → workbench robustness → lifecycle/trade manager → certify → CHI404). This is not the active HBT-only order unless an owner explicitly re-enables that route. Code registry: `packages/backtest_pipeline/src/research_pipeline_stages.py`.

**Verification honesty:** Every agent handoff must use the status block in [VALIDATION_HONESTY.md](../VALIDATION_HONESTY.md). Scope-green commands for each lane are in that doc; smoke-only targeted pytest never substitutes.

Baseline metrics: [CPI_2024_09_11_TIGHT_BASELINE.md](CPI_2024_09_11_TIGHT_BASELINE.md)

## 1. VectorBT screen -> HftBacktest realism (historical prior path)

In the historical path, broad screening, refine, all-model, and paid-compute
research started with the VectorBT handoff artifact. Broad scopes required the
Rust VectorBT engine and failed closed without it. Execution-realism evidence
then moved through the official HftBacktest source-lock/data/latency/fill gates
in [HFTBACKTEST_REALISM_ENGINE_SPEC.md](../project/HFTBACKTEST_REALISM_ENGINE_SPEC.md).
This section is inactive for the active HBT-only plan unless an owner explicitly
re-enables the legacy route.

Retired hft3 replay entrypoints such as `scripts/run_event_replay.py`,
`scripts/run_event_universe.py`, `replay_matrix.py`, and `ReplaySession` are
historical only for this path. Do not use them as active research, fallback, or
new workbench execution routes.

```bash
python scripts/run_pipeline.py \
  --thesis "Fade spread blowout after CPI surprise on MES" \
  --event-id CPI_2024_09_11_TIGHT \
  --vectorbt \
  --hftbacktest-realism \
  --hftbacktest-data-npz <validated_hftbacktest_npz> \
  --hftbacktest-latency-model <measured_latency_model.json> \
  --hftbacktest-fill-queue-model <fill_queue_model.json> \
  --hftbacktest-upstream-ref v2.4.2 \
  --native-hot-path-evidence reports/latency_baselines/order_ack_campaign_20260611T072116Z_summary.json#sha256:<digest>
```

Equivalent handoff when a terminal screening artifact already exists:

```bash
python scripts/run_hftbacktest_realism.py \
  --screening-artifact research_cards/pipeline_runs/<run_id>/screening_artifact.json \
  --data-npz <validated_hftbacktest_npz> \
  --latency-model <measured_latency_model.json> \
  --fill-queue-model <fill_queue_model.json> \
  --hftbacktest-upstream-ref v2.4.2 \
  --native-hot-path-evidence reports/latency_baselines/order_ack_campaign_20260611T072116Z_summary.json#sha256:<digest>
```

This command now writes the HBT-0 through HBT-4 artifact set when supplied with
valid inputs: source lock, data validation, latency model, fill/queue model,
`official_replay.json`, orders/fills/markouts, discrepancies, and summary. It
still fails closed unless the input is a Rust VectorBT screen-passed handoff,
official HftBacktest replay runs non-accelerated, and the source lock contains
hash-backed native C++ hot-path evidence. Exit code `2` remains expected for
honest `research_only`, `market_impact_not_modeled`, or failing gate states.

## 1a. Macro event replay (retired historical path)

**Backtester certification:** Every replay output includes a `certification_stamp` (T1). See [BACKTESTER_CERTIFICATION.md](BACKTESTER_CERTIFICATION.md) for T0–T4 gates.

**Status:** Retired for active VectorBT/HftBacktest research. Kept only so
older artifacts and audit notes remain interpretable.

**Historical use:** Backtest a scheduled macro window (CPI, NFP, etc.) on
Databento MBO with CHI404-measured latency.

```bash
# Historical only; do not use for new VectorBT/HftBacktest work.
python scripts/run_event_replay.py \
  --event-id CPI_2024_09_11_TIGHT \
  --chi404-summary runtime/latency_reports/latency_summary.json
```

- Resolves window from `data_system/config/events.csv` (never `date +%F` as research key).
- **Historical primary engine:** `replay_execution_adapter` (`ReplaySession` + `HftBacktestSimulatedExchangeAdapter` + combined hypotheses).
- **Historical secondary engine:** `per_hypothesis_replay` (same adapter contract, one hypothesis per session).
- Historical output: `research_cards/<event_id>_replay/`
- Historical lifecycle audits: `runtime/replay_audits/{run_id}_order_lifecycle.jsonl`

Equivalent:

```bash
# Historical only; do not use for new VectorBT/HftBacktest work.
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
# Historical only; do not use for new VectorBT/HftBacktest work.
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

Or via hybrid wrapper: `python scripts/run_hybrid_pipeline_gate.py --tier smoke --event-id CPI_2024_09_11_TIGHT`. See [docs/structural_models/FULL_PIPELINE_GATE.md](../structural_models/FULL_PIPELINE_GATE.md).

**Verify (scope-green):**

```bash
# Historical verify only.
python -m pytest tests/test_run_event_replay.py tests/test_replay_must_emit_order_intents.py tests/test_replay_clock_order_timestamps.py -q
python scripts/run_hybrid_pipeline_gate.py --event-id CPI_2024_09_11_TIGHT  # when touching hybrid gate
```

## 1b. Autoresearch pipeline (legacy NL thesis or dry-run only)

**Status:** For active VectorBT/HftBacktest research, use section 1 with
`--vectorbt`. The no-VectorBT command below is retained for historical
interpretation and dry-run candidate parsing only; it must not be used as a
full research/backtest path.

```bash
# Parse + candidates only (no backtest)
python scripts/run_pipeline.py \
  --thesis "Fade spread blowout after CPI" \
  --event-id CPI_2024_09_11_TIGHT \
  --dry-run --no-llm
```

Optional document ingestion is dry-run only unless section 1's VectorBT/HftBacktest
handoff flags are also supplied:

```bash
python scripts/run_pipeline.py \
  --thesis "..." \
  --doc docs/references/dev_instructions.pdf \
  --event-id CPI_2024_09_11_TIGHT \
  --dry-run --no-llm
```

Output: `research_cards/pipeline_runs/<run_id>/`. Authority: [AUTORESEARCH_PIPELINE.md](../research/AUTORESEARCH_PIPELINE.md), source PDF [dev_instructions.pdf](../references/dev_instructions.pdf).

**Verify (scope-green):** `python -m pytest tests/test_research_pipeline.py -q`

## 1c. Low-float runner (moved out / historical)

**Status:** Not an active hft3 research entrypoint. hft3's canonical active
research path in this repo is CME VectorBT screen -> HftBacktest realism. The
low-float equities lane was moved to `hft3-equities-lane`; use that repository's
own entrypoint docs if mounted. Historical hft3 references remain only so old
artifacts are interpretable.

Historical output: `research_cards/equities/<run_id>/`. Authority:
[LOW_FLOAT_RUNNER.md](../research/LOW_FLOAT_RUNNER.md), source PDF
[low_float_momentum_anomaly_research_pack.pdf](../references/low_float_momentum_anomaly_research_pack.pdf).

## 1d. Crypto alpha (moved out / historical)

**Status:** Not an active hft3 research entrypoint. hft3's canonical active
research path in this repo is CME VectorBT screen -> HftBacktest realism. The
crypto lane was moved to `hft3-crypto-lane`; use that repository's own entrypoint
docs if mounted. Historical hft3 references remain only so old artifacts are
interpretable.

Historical hypotheses: `research/hypotheses/crypto_alpha_engine_extracted_hypotheses.yaml`.
Historical manifest: `research/hypotheses/crypto_alpha_engine_manifest.yaml`.
Historical report: [crypto_alpha_engine_extraction_report.md](../../research/reports/crypto_alpha_engine_extraction_report.md).

## 2. Single-hypothesis drill-down (historical/non-primary)

**Status:** Historical diagnostic only. Do not use as the active
VectorBT/HftBacktest research path.

```bash
python scripts/run_single_hyp_backtest.py \
  --event-id CPI_2024_09_11_TIGHT \
  --chi404-summary runtime/latency_reports/latency_summary.json
```

Uses `SignalBacktester` only (not `ReplayRunner`).

## 3. Full hypothesis matrix (historical/non-primary)

**Status:** Historical diagnostic only. Do not use as the active
VectorBT/HftBacktest research path.

```bash
python backtest_pipeline/src/research_runner.py \
  --data data/npz/MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz \
  --skip-hft
```

Or orchestrated:

```bash
python scripts/run_offline_pipeline.py --skip-download --event-id CPI_2024_09_11_TIGHT
```

Uses `SignalBacktester` per hypothesis. This is not an official HftBacktest
realism handoff and is not an active full-research substitute.

## 4. Rithmic trial live (CHI404 only)

**When:** Paper-forward capture + optional replay after live session.

```bash
# On CHI404 — tag manifest with macro event_id; replay uses Databento NPZ + CHI404 latency
EVENT_ID=CPI_2024_09_11_TIGHT bash scripts/chi404_run_trial_live.sh
```

- `--date` / folder `YYYY-MM-DD` = **capture session date** (ingest only).
- `EVENT_ID` / `replay-event` = **research event** (from `events.csv`).

**Paper order latency:** [CHI404_CANONICAL_ENTRYPOINTS.md](CHI404_CANONICAL_ENTRYPOINTS.md) — build and run native `rithmic_latency_probe` on CHI404. The compatibility sweep script refuses Python/ctypes hot-path measurement. No synthetic log inject.

**Verify (scope-green workstation):** `python -m pytest tests/test_rithmic_trial_pipeline.py tests/test_rithmic_topology_guards.py -q`. CHI404 PASS requires `validate_pass_criteria.py` on real logs — see [docs/chi404/VALIDATION_ADDENDUM.md](../chi404/VALIDATION_ADDENDUM.md).

## 5. Legacy / smoke only (do not use for macro research)

| Path | Role | Why not for CPI research |
|------|------|---------------------------|
| `pipeline replay-sample --simple` | Trial NPZ smoke | Trade-only trial capture; wrong calendar; no event_id |
| `ReplayRunner` alone | Queue fill experiment | Was depth-only + mean@0.25 before fix; retired for new VectorBT/HftBacktest work |
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
| Macro MBO replay body | Databento | `<npz_root>/<symbol>_<event_id>_mbo.npz` — `C:\hft3-lake\npz` via `HFT3_NPZ_ROOT` since 2026-06-12 (see [DATA_LAKE_3TIER.md](DATA_LAKE_3TIER.md)) |
| Colo latency | CHI404 probe | `runtime/latency_reports/latency_summary.json` |
| Rithmic live tape | CHI404 capture | `data/raw/rithmic_trial_live_capture/` (forward-only); CC2 `.cap` archive to B2 nightly, 30-day local retention |

No historical Rithmic download for past CPI dates.

## PDF structural models (signal layer, not HYP backtest wiring)

Seven models from [algorithmic_trading_strategy_development.pdf](../references/algorithmic_trading_strategy_development.pdf) are separate from the 44 `HYP_*` families:

- Code: `features_engine/src/structural_models/`
- Specs: [docs/structural_models/PDF_MODELS.md](../structural_models/PDF_MODELS.md)
- Registry: `get_structural_models()` (not `get_active_hypotheses()`)

Historical macro event replay (`run_event_replay.py`) ran HYP backtests by
default. For new VectorBT/HftBacktest research, use the primary handoff above.
PDF hybrid replay notes remain in [PDF_HYBRID_REPLAY.md](../structural_models/PDF_HYBRID_REPLAY.md)
for artifact interpretation only.

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
- Does **not** replace the VectorBT/HftBacktest realism path; use workbench for
  per-model latency viability and exploratory promotion gates only

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
