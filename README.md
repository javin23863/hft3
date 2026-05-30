# hft3

Chicago CME microstructure research and execution stack.

**New developer?** Read **[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)** top to bottom once, then use **[docs/DOC_INDEX.md](docs/DOC_INDEX.md)** as the chronological doc map.

## Quick start

```bash
git clone --recurse-submodules https://github.com/javin23863/hft3.git
cd hft3
cp .env.example .env    # configure locally; never commit
pip install -r data_system/requirements.txt \
            -r backtest_pipeline/requirements.txt \
            -r workbench/requirements.txt
pip install graphifyy   # recommended for code navigation
git submodule update --init vendor/openfoundry vendor/alphageometry
python -m pytest tests/ -q
```

## System overview

```
                    ┌─────────────────────────────────────────┐
                    │     Authority specs (docs/references/)   │
                    │  BLUEPRINT · CME PDFs · Rithmic trial   │
                    └────────────────────┬────────────────────┘
                                         │
     ┌───────────────┬───────────────────┼───────────────────┬───────────────┐
     ▼               ▼                   ▼                   ▼               ▼
 Research        Features           Backtest            Workbench         CHI404
 (Databento)     (MBO + regime)     (HftBacktest)       (campaigns)       (bare metal)
 data_system/    features_engine/  backtest_pipeline/  workbench/        infrastructure/
     │               │                   │              data_layer/            │
     └───────────────┴───────────────────┴───────────────┴───────────────────┘
                                         │
                              Rithmic trial (quarantined interim lane)
                              data_system/rithmic_trial/
```

Post-run after-action (workstation only): `data_layer/` → Ollama Hawkish-8B + file KG. See [docs/workbench/AFTER_ACTION_REPORTS.md](docs/workbench/AFTER_ACTION_REPORTS.md).

## Reference documents

| Document | Purpose |
|----------|---------|
| [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) | **Start here** — full operational guide (chronological) |
| [docs/DOC_INDEX.md](docs/DOC_INDEX.md) | Every doc in recommended reading order |
| [docs/references/](docs/references/README.md) | Canonical PDF bundle + MANIFEST |
| [BLUEPRINT.md](BLUEPRINT.md) | Developer handoff summary |
| [AGENTS.md](AGENTS.md) | Agent charter and topology rules |

## Major subsystems

| Subsystem | Entry point | Docs |
|-----------|-------------|------|
| Research ingest | `data_system/src/databento_client.py` | [GETTING_STARTED §5](docs/GETTING_STARTED.md#5-research-pipeline-offline) |
| Feature engine | `features_engine/src/pipeline/market_state_pipeline.py` | [BLUEPRINT.md](BLUEPRINT.md) |
| Backtest | `backtest_pipeline/src/runner.py` | [GETTING_STARTED §5](docs/GETTING_STARTED.md#5-research-pipeline-offline) |
| Workbench | `python -m workbench run` | [docs/workbench/README.md](docs/workbench/README.md) |
| After-action LLM | `data_layer/pipeline/after_action.py` | [AFTER_ACTION_REPORTS.md](docs/workbench/AFTER_ACTION_REPORTS.md) |
| CHI404 tuning | `infrastructure/chi404/run_chi404_tuning.sh` | [GETTING_STARTED §8](docs/GETTING_STARTED.md#8-chi404-production-infra) |
| Rithmic trial | `python -m data_system.rithmic_trial.pipeline` (live: **CHI404 only**) | [docs/rithmic_trial/README.md](docs/rithmic_trial/README.md) |
| Code graph | `graphify query "..."` | [docs/GRAPHIFY_WORKFLOW.md](docs/GRAPHIFY_WORKFLOW.md) |
| Agent workflow | [AGENTS.md](AGENTS.md) | [docs/AGENTIC_ENGINEERING.md](docs/AGENTIC_ENGINEERING.md) |

## Common commands

```bash
# Tests
python -m pytest tests/ -q
pytest tests/test_workbench/ tests/test_data_layer/ -q -m "not slow"

# Workbench full sweep (enables after-action on workstation)
python -m workbench run --model HYP_5 --event-id CPI_2024_09_11_TIGHT --full-sweep

# CHI404 — sync repo to bare-metal server
bash scripts/sync_chi404_repo.sh

# Graphify — before edits
graphify query "where is X defined?"
# after code edits (no cloud API)
.\scripts\graphify_rebuild.ps1        # Windows
graphify update .                    # any platform
```

## Contributing

1. Read [AGENTS.md](AGENTS.md) and [docs/REVIEWER_CHARTER.md](docs/REVIEWER_CHARTER.md).
2. Run `graphify query` before locating code; run `graphify update .` after edits.
3. Keep the Rithmic trial lane quarantined from production `data/npz/`.
4. Verify with `pytest` before pushing.
