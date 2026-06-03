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

## Current layout

```
hft3/
├── apps/                    # Runnable applications (`workbench`, observer)
├── backtests/               # Backtest configs, including crypto hypotheses
├── configs/                 # Research, risk, and execution configuration
├── data/                    # Local/quarantined data roots
├── docs/                    # Human, AI, vault, lane, research, and validation docs
├── infrastructure/          # CHI404 and edge service deployment assets
│   └── crypto_lane/         # Consolidated BTC edge daemon/receiver units
├── integrations/            # OpenFoundry/domain-pack integration assets
├── packages/                # Python/Rust packages and lane libraries
│   ├── crypto_lane/         # Crypto lane ingestion + Rust edge daemon
│   ├── equities_lane/       # Low-float equities lane + historical runner tools
│   ├── hft3/                # Validation governance package
│   ├── research_pipeline/   # Research intake/generation/reasoning
│   └── ...                  # Data, feature, execution, options, trade packages
├── research/                # Hypothesis manifests and research source material
├── research_cards/          # Legacy research-card artifacts still in use
├── rithmic_gateway/         # C++ execution hot path; do not touch casually
├── runtime/                 # Certification, latency, and audit state
├── scripts/                 # Research/data/validation entrypoints
├── telemetry/               # Telemetry artifacts/configs
├── tests/                   # Unit, lane, validation, and integration tests
├── tools/                   # Ops, graphify, migration, and shell helpers
├── vendor/                  # Vendored references/submodules
├── graphify-out/            # Code graph and wiki for agents
└── BLUEPRINT.md             # System spec
```

## Consolidated branch payloads

The former feature branches are consolidated into `main` in chronological commit order:

| Former branch | Current home |
|---------------|--------------|
| `btc-edge-live-processing` | `packages/crypto_lane/edge_daemon/`, `packages/crypto_lane/src/ingest/edge_receiver.py`, `infrastructure/crypto_lane/`, `tests/test_crypto_lane/test_edge_receiver_deserialize.py` |
| `historical-cohort-benchmark` | `packages/equities_lane/src/prediction/historical_cohort_benchmark.py`, `tests/test_equities_lane/test_historical_cohort_benchmark.py`, `packages/equities_lane/pipeline.py historical-cohort-benchmark` |
| `runner-seed-resolver` | `packages/equities_lane/src/prediction/runner_seed_resolver.py`, `packages/equities_lane/config/historical_runner_benchmark.yaml`, `scripts/fetch_*`, `tests/test_equities_lane/test_*runner*` |

## Legacy paths (shim period)

During migration, old import paths remain valid via shims and `pyproject.toml`:

| Legacy | Target |
|--------|--------|
| `workbench/` | `apps/workbench/` |
| `features_engine/` | `packages/features_engine/` |
| `research_cards/` | Prefer `artifacts/` for new durable outputs; legacy cards remain in use |
| `scripts/run_*.py` | Keep current script entrypoints unless a migration PR moves them |

Set `HFT3_ARTIFACTS_ROOT` to override artifact root (default: `artifacts/`).

## Output directories

| Path | Contents | Git |
|------|----------|-----|
| `artifacts/workbench_runs/` | Campaign and single-run JSON, parquet, reports | Ignored (local) |
| `artifacts/kg/` | File-backed knowledge graph JSONL | Structure committed |
| `artifacts/event_replays/` | Macro event replay cards | Mixed |
| `research_cards/` | Existing workbench/lane research cards | Mixed legacy |
| `runtime/validation/` | Backtester certification registry, scorecards | Committed |
| `runtime/latency_reports/` | CHI404 latency summaries | Committed when present |
| `runtime/replay_audits/` | Replay session lifecycle JSONL | Ignored |

## Model IDs

Canonical IDs are **descriptive slugs** (e.g. `SPREAD_BLOWOUT_RECOMPRESSION`), not `HYP_N`. Registry: `features_engine/config/model_registry.yaml`.

## Scripts by audience

| Audience | Location |
|----------|----------|
| Research / replay | `scripts/` — `run_event_replay.py`, pipeline helpers, data fetchers |
| Workbench | `python -m workbench` |
| CHI404 ops | `tools/chi404/` |
| Certification | `scripts/run_backtester_fast_gate.sh` (→ `tools/certification/`) |
| Graphify | `tools/graphify/` |
| Shell timeouts / bounded verify | `tools/shell/` · `scripts/run_agent_verify.ps1` · [docs/ai/SHELL_EXECUTION.md](../ai/SHELL_EXECUTION.md) |

See [human/RUNTIME_CONTRACT.md](human/RUNTIME_CONTRACT.md) for backend ↔ UI artifact schema.
