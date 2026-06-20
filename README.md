# hft3

Chicago CME microstructure research and execution stack.

## Choose Your Path

| You are | Start here |
|---------|------------|
| **Human developer** | [docs/START_HERE.md](docs/START_HERE.md) -> [docs/human/GETTING_STARTED.md](docs/human/GETTING_STARTED.md) -> [docs/human/DOC_INDEX.md](docs/human/DOC_INDEX.md) |
| **ANY LLM agent session start** | [docs/vault/AGENT_RUNTIME_ROADMAP.md](docs/vault/AGENT_RUNTIME_ROADMAP.md) → [00-fable-mindset.mdc](.cursor/rules/00-fable-mindset.mdc) → [01-ponytail-mindset.mdc](.cursor/rules/01-ponytail-mindset.mdc) → [AGENTS.md](AGENTS.md) |

Coding style: [docs/ai/ENGINEERING.md](docs/ai/ENGINEERING.md). Contributing:
[CONTRIBUTING.md](CONTRIBUTING.md).

## Quick Start

```bash
git clone --recurse-submodules https://github.com/javin23863/hft3.git
cd hft3
cp .env.example .env
pip install -r packages/data_system/requirements.txt \
            -r packages/backtest_pipeline/requirements.txt \
            -r apps/workbench/requirements.txt
pip install graphifyy
git submodule update --init vendor/openfoundry vendor/alphageometry
python -m pytest tests/ -q
python tools/graphify/build_wiki_index.py
```

## Fresh Research State

Before all-model or all-lane research, clear stale generated evidence through
the Workbench boundary:

```bash
python -m apps.workbench fresh-start --confirm-hard-delete --json
python -m apps.workbench leakage-detect --json
```

This creates a fresh active-run manifest and prevents prior-run artifacts from
being treated as current evidence. Details: [docs/LEAKAGE_DETECTION.md](docs/LEAKAGE_DETECTION.md).

## Repository Map

Full layout: [docs/REPO_MAP.md](docs/REPO_MAP.md)

```text
apps/              runnable apps, including cockpit and workbench
packages/          Python libraries and lane packages
scripts/           operational and research entrypoints
infrastructure/    CHI404 and edge service deployment assets
artifacts/         durable generated research outputs
research_cards/    legacy research-card outputs still in use
runtime/           validation, latency, audit, and machine-local state
graphify-out/      code graph for agents
```

## Common Commands

```bash
# Workbench
python -m workbench run --model SPREAD_BLOWOUT_RECOMPRESSION --event-id CPI_2024_09_11_TIGHT --full-sweep

# Macro replay
python scripts/run_event_replay.py --event-id CPI_2024_09_11_TIGHT

# Autoresearch pipeline
python scripts/run_pipeline.py --thesis "Fade spread blowout after CPI" --event-id CPI_2024_09_11_TIGHT --dry-run

# T0 certification gate
python -m pytest tests/backtester_validation/fast -q

# Graph
graphify query "where is ReplaySession defined?"
```

## Reference Documents

| Document | Purpose |
|----------|---------|
| [BLUEPRINT.md](BLUEPRINT.md) | System spec |
| [docs/references/](docs/references/README.md) | Canonical PDF bundle |
| [docs/human/RUNTIME_CONTRACT.md](docs/human/RUNTIME_CONTRACT.md) | Artifact schema |
| [docs/vault/BACKTESTER_CERTIFICATION.md](docs/vault/BACKTESTER_CERTIFICATION.md) | Certification tiers |
| [docs/cockpit/BUILDOUT_REVIEW.md](docs/cockpit/BUILDOUT_REVIEW.md) | Cockpit chronology and commit ledger |

Post-run after-action: [docs/workbench/AFTER_ACTION_REPORTS.md](docs/workbench/AFTER_ACTION_REPORTS.md).

Autoresearch pipeline: [docs/research/AUTORESEARCH_PIPELINE.md](docs/research/AUTORESEARCH_PIPELINE.md).
