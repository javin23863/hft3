# Repository map

Single source of truth for top-level layout. Chronological human path: [human/DOC_INDEX.md](human/DOC_INDEX.md). AI path: [ai/ONBOARDING.md](ai/ONBOARDING.md).

## Entry points

| File | Audience |
|------|----------|
| [README.md](../README.md) | Human vs AI fork |
| [docs/human/GETTING_STARTED.md](human/GETTING_STARTED.md) | Human — read once top-to-bottom |
| [docs/ai/ONBOARDING.md](ai/ONBOARDING.md) | AI — graph first |
| [AGENTS.md](../AGENTS.md) | Agent charter |
| [graphify-out/wiki/index.md](../graphify-out/wiki/index.md) | AI graph index (freshness banner) |

## Layout

```
hft3/
├── src/hft3/                # Importable namespace (consolidation layer)
│   ├── pipelines/           # Lane pipelines (equities, crypto, …)
│   ├── models/              # Features, structural models, hypotheses
│   ├── backtest/            # Backtest runners, certification
│   ├── connectors/          # Data connectors (Databento, Rithmic, …)
│   ├── validation/          # Certification registry, gates
│   └── data/                # Data resolution, NPZ management
├── apps/                    # Runnable applications
│   ├── workbench/           # CLI + Streamlit UI (from workbench/)
│   └── cli/                 # User-facing scripts (from scripts/ entrypoints)
├── packages/                # Python libraries (legacy — shim period)
│   ├── data_system/
│   ├── features_engine/
│   ├── backtest_pipeline/
│   ├── replay/
│   ├── execution/
│   ├── data_layer/
│   ├── decision_engine/
│   ├── hfc3/
│   ├── options_lane/
│   ├── backtest/
│   └── hft3/                # Validation governance package
├── configs/                 # Centralised configuration
│   ├── equities/            # universe.yaml, decadal_runners.yaml
│   ├── crypto/              # universe.yaml, lake_sources.yaml
│   ├── options/             # parity_universe.yaml
│   ├── futures/             # rithmic_trial.yaml
│   └── features/            # model_registry.yaml, pdf_model_params.yaml
├── docs/                    # Documentation
│   ├── human/               # Human onboarding, runtime contract
│   ├── ai/                  # AI onboarding, engineering standards
│   ├── vault/               # Deep reference (certification, CHI404)
│   ├── reference/           # Canonical PDF bundle
│   ├── guides/              # Task-oriented walkthroughs
│   └── specs/               # System specifications
├── tools/                   # Ops, migration, graphify helpers
├── artifacts/               # Research outputs (from research_cards/)
├── runtime/                 # Ephemeral machine state
├── infrastructure/          # CHI404 bare metal
├── tests/
├── graphify-out/
├── vendor/
├── integrations/
├── rithmic_gateway/         # C++ execution hot path
├── telemetry/
└── BLUEPRINT.md
```

## Legacy paths (shim period)

During migration, old import paths remain valid:

| Legacy | Target |
|--------|--------|
| `packages/*` | `packages/*` (unchanged — backward compat) |
| `src/hft3/*` | New canonical imports (`hft3.pipelines.*`, etc.) |
| `configs/*` | Centralised configs (originals remain under `packages/*/config/`) |

Set `HFT3_ARTIFACTS_ROOT` to override artifact root (default: `artifacts/`).

## Output directories

| Path | Contents | Git |
|------|----------|-----|
| `artifacts/workbench_runs/` | Campaign and single-run JSON, parquet, reports | Ignored (local) |
| `artifacts/kg/` | File-backed knowledge graph JSONL | Structure committed |
| `artifacts/event_replays/` | Macro event replay cards | Mixed |
| `runtime/validation/` | Backtester certification registry, scorecards | Committed |
| `runtime/latency_reports/` | CHI404 latency summaries | Committed when present |
| `runtime/replay_audits/` | Replay session lifecycle JSONL | Ignored |

## Model IDs

Canonical IDs are **descriptive slugs** (e.g. `SPREAD_BLOWOUT_RECOMPRESSION`), not `HYP_N`. Registry: `configs/features/model_registry.yaml`.

## Scripts by audience

| Audience | Location |
|----------|----------|
| Research / replay | `apps/cli/` — `run_event_replay.py`, etc. |
| Workbench | `python -m workbench` |
| CHI404 ops | `tools/chi404/` |
| Certification | `scripts/run_backtester_fast_gate.sh` (→ `tools/certification/`) |
| Graphify | `tools/graphify/` |
| Shell timeouts / bounded verify | `tools/shell/` · `scripts/run_agent_verify.ps1` · [docs/ai/SHELL_EXECUTION.md](../ai/SHELL_EXECUTION.md) |

See [human/RUNTIME_CONTRACT.md](human/RUNTIME_CONTRACT.md) for backend ↔ UI artifact schema.
