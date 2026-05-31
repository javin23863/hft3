# hft3

Chicago CME microstructure research and execution stack.

## Choose your path

| You are | Start here |
|---------|------------|
| **Human developer** | [docs/human/GETTING_STARTED.md](docs/human/GETTING_STARTED.md) → [docs/human/DOC_INDEX.md](docs/human/DOC_INDEX.md) |
| **AI / Cursor agent** | [graphify-out/wiki/index.md](graphify-out/wiki/index.md) → [docs/ai/ONBOARDING.md](docs/ai/ONBOARDING.md) → [AGENTS.md](AGENTS.md) |

Coding style (Karpathy): [docs/ai/ENGINEERING.md](docs/ai/ENGINEERING.md). Contributing: [CONTRIBUTING.md](CONTRIBUTING.md).

## Quick start

```bash
git clone --recurse-submodules https://github.com/javin23863/hft3.git
cd hft3
cp .env.example .env
pip install -r data_system/requirements.txt \
            -r backtest_pipeline/requirements.txt \
            -r workbench/requirements.txt
pip install graphifyy
git submodule update --init vendor/openfoundry vendor/alphageometry
python -m pytest tests/ -q
python tools/graphify/build_wiki_index.py
```

## Repository map

Full layout: [docs/REPO_MAP.md](docs/REPO_MAP.md)

```
apps/workbench     CLI + Streamlit UI
packages/*         Python libraries (features, backtest, replay, …)
artifacts/         Research outputs
runtime/           Certification, latency, audits
graphify-out/      Code graph (AI entry)
```

## Common commands

```bash
# Workbench (canonical model slug)
python -m workbench run --model SPREAD_BLOWOUT_RECOMPRESSION --event-id CPI_2024_09_11_TIGHT --full-sweep

# Macro replay
python scripts/run_event_replay.py --event-id CPI_2024_09_11_TIGHT

# Autoresearch pipeline (NL thesis → backtest → artifacts)
python scripts/run_pipeline.py --thesis "Fade spread blowout after CPI" --event-id CPI_2024_09_11_TIGHT --dry-run

# T0 certification gate
python -m pytest tests/backtester_validation/fast -q

# Graph (AI)
graphify query "where is ReplaySession defined?"
```

## Reference documents

| Document | Purpose |
|----------|---------|
| [BLUEPRINT.md](BLUEPRINT.md) | System spec |
| [docs/references/](docs/references/README.md) | Canonical PDF bundle |
| [docs/human/RUNTIME_CONTRACT.md](docs/human/RUNTIME_CONTRACT.md) | Artifact schema |
| [docs/vault/BACKTESTER_CERTIFICATION.md](docs/vault/BACKTESTER_CERTIFICATION.md) | Certification tiers |

Post-run after-action (workstation + Ollama): [docs/workbench/AFTER_ACTION_REPORTS.md](docs/workbench/AFTER_ACTION_REPORTS.md).

Autoresearch pipeline (NL hypothesis → backtest): [docs/research/AUTORESEARCH_PIPELINE.md](docs/research/AUTORESEARCH_PIPELINE.md).
