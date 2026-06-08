# hft3

Chicago CME microstructure research and execution stack.

> **STOP — [KNOWN_GAPS.md](KNOWN_GAPS.md)** lists data missing on disk, CHI404 vs workstation topology, which **workbench slugs** need equities OPRA (`DEALER_HEDGING` only), imbalance/crypto issues, and pipeline bugs. Model CLI ids: `python -m workbench list` (not `HYP_N`). Read before claiming research or data work is complete.

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
src/hft3/          Importable namespace (consolidation over packages/)
apps/workbench     CLI + Streamlit UI
packages/*         Python libraries (features, backtest, replay, …) — legacy
configs/           Centralised configuration (YAML/JSON)
artifacts/         Research outputs
runtime/           Certification, latency, audits
graphify-out/      Code graph (AI entry)
```

## Common commands

Macro events (55 in `packages/data_system/config/events.csv` — 19 CPI, 33 NFP, 3 prop-flatten):

```bash
python packages/data_system/src/macro_event_cli.py   # list all event_id values
```

```bash
# Pick any event_id from the catalog (examples: CPI or NFP)
export EVENT_ID=NFP_2024_01_05_TIGHT

python -m workbench run --model SPREAD_BLOWOUT_RECOMPRESSION --event-id "$EVENT_ID" --full-sweep
python scripts/run_event_replay.py --event-id "$EVENT_ID"
python scripts/run_pipeline.py --thesis "Fade spread blowout after release" --event-id "$EVENT_ID" --dry-run

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
