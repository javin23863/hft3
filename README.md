# hft3

Chicago CME microstructure research and execution stack.

**New here?** Start with **[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)** — it walks through setup, the research pipeline, CHI404 infra, Rithmic trial capture, graphify, and verification from start to finish.

## Quick start

```bash
git clone https://github.com/javin23863/hft3.git
cd hft3
cp .env.example .env    # configure locally; never commit
pip install -r data_system/requirements.txt -r backtest_pipeline/requirements.txt
python -m pytest tests/ -q
```

## System overview

```
                    ┌─────────────────────────────────────────┐
                    │           Reference specs (PDFs)         │
                    │  BLUEPRINT · CME production · Rithmic    │
                    └────────────────────┬────────────────────┘
                                         │
     ┌───────────────┬───────────────────┼───────────────────┬───────────────┐
     ▼               ▼                   ▼                   ▼               ▼
 Research        Features           Backtest            Decision         CHI404
 (Databento)     (MBO + regime)     (HftBacktest)       (train)          (bare metal)
 data_system/    features_engine/  backtest_pipeline/  decision_engine/ infrastructure/
     │               │                   │                   │               │
     └───────────────┴───────────────────┴───────────────────┴───────────────┘
                                         │
                              Rithmic trial (quarantined interim lane)
                              data_system/rithmic_trial/
```

## Reference documents

| Document | Purpose |
|----------|---------|
| [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) | **Start here** — full operational guide |
| [docs/AUDIT_FRICTION_REPORT.md](docs/AUDIT_FRICTION_REPORT.md) | Layer audit findings and remediation status |
| [BLUEPRINT.md](BLUEPRINT.md) | Developer handoff summary |
| [chicago_cme_a_plus_production_implementation_prompt.pdf](chicago_cme_a_plus_production_implementation_prompt.pdf) | Production build spec |
| [rithmic_trial_hftbacktest_pipeline_prompt.pdf](rithmic_trial_hftbacktest_pipeline_prompt.pdf) | Rithmic trial → HftBacktest wiring (R\|Trader bridge until R\|API) |

## Major subsystems

| Subsystem | Entry point | Docs |
|-----------|-------------|------|
| Research ingest | `data_system/src/databento_client.py` | [GETTING_STARTED §4](docs/GETTING_STARTED.md#4-research-pipeline-offline) |
| Feature engine | `features_engine/src/pipeline/market_state_pipeline.py` | [BLUEPRINT.md](BLUEPRINT.md) |
| Backtest | `backtest_pipeline/src/runner.py` | [GETTING_STARTED §4](docs/GETTING_STARTED.md#4-research-pipeline-offline) |
| CHI404 tuning | `infrastructure/chi404/run_chi404_tuning.sh` | [GETTING_STARTED §5](docs/GETTING_STARTED.md#5-chi404-production-infra) |
| Rithmic trial | `python -m data_system.rithmic_trial.pipeline` (live capture: **CHI404 only**) | [docs/rithmic_trial/README.md](docs/rithmic_trial/README.md) |
| Code graph | `graphify query "..."` | [docs/GRAPHIFY_WORKFLOW.md](docs/GRAPHIFY_WORKFLOW.md) |
| Agent workflow | [AGENTS.md](AGENTS.md) | [docs/AGENTIC_ENGINEERING.md](docs/AGENTIC_ENGINEERING.md) |

## Common commands

```bash
# Tests
python -m pytest tests/ -q

# CHI404 — sync repo to bare-metal server
bash scripts/sync_chi404_repo.sh

# Rithmic trial — CHI404 live setup (on server only)
bash scripts/setup_rithmic_chi404.sh

# Rithmic trial — fixture capture (workstation / CI, no live broker)
python -m data_system.rithmic_trial.pipeline capture --config data_system/config/rithmic_trial.yaml
python -m data_system.rithmic_trial.pipeline process --date YYYY-MM-DD --symbol MES

# Graphify — before edits
graphify query "where is X defined?"
# after edits
.\scripts\graphify_rebuild.ps1        # Windows
graphify update .                    # any platform
```

## Contributing

1. Read [AGENTS.md](AGENTS.md) and [docs/REVIEWER_CHARTER.md](docs/REVIEWER_CHARTER.md).
2. Run `graphify query` before locating code; run `graphify update .` after edits.
3. Keep the Rithmic trial lane quarantined from production `data/npz/`.
4. Verify with `pytest` before pushing.
