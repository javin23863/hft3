# Getting Started — hft3 end-to-end

Chicago CME microstructure research and execution stack. Read this once, top to bottom, before touching code.

## 1. What this repo is

hft3 implements the [BLUEPRINT.md](../BLUEPRINT.md) specification for:

| Lane | Purpose | Primary paths |
|------|---------|---------------|
| **Research** | Databento MBO → NPZ → features → backtest | `data_system/`, `features_engine/`, `backtest_pipeline/` |
| **Decision** | Feature store, targets, model training | `decision_engine/python/` |
| **Production infra** | CHI404 bare-metal tuning and validation | `infrastructure/chi404/`, `scripts/sync_chi404_repo.sh` |
| **Rithmic trial** | Interim live capture until R\|API (quarantined) | `data_system/rithmic_trial/`, `docs/rithmic_trial/` |
| **Telemetry** | Latency and health dashboards | `telemetry/` |

Authoritative math and production specs live in the PDFs at repo root — not duplicated here.

## 2. Prerequisites

- **Python 3.11+** with `pip`
- **Git** with LF normalization (`.gitattributes` enforces this)
- **Optional:** CMake + C++17 compiler for `features_engine/cpp/`
- **Optional:** `graphify` CLI (`pip install "graphifyy[pdf]"`) for code navigation
- **For CHI404:** SSH access to bare-metal server (`Host chi404` in `~/.ssh/config`)
- **For Rithmic trial:** CHI404 only (R\|Trader Wine on colo) — not a Windows workstation; see [docs/rithmic_trial/README.md](rithmic_trial/README.md)

## 3. First-time setup

```bash
git clone https://github.com/javin23863/hft3.git
cd hft3
cp .env.example .env          # fill in keys locally; never commit .env
pip install -r data_system/requirements.txt
pip install -r backtest_pipeline/requirements.txt
pip install "graphifyy[pdf]"  # optional but recommended
```

Run the test suite to confirm the environment:

```bash
python -m pytest tests/ -q
```

## 4. Research pipeline (offline)

**Canonical entrypoints:** [docs/vault/RESEARCH_ENTRYPOINTS.md](vault/RESEARCH_ENTRYPOINTS.md)  
**CPI baseline:** [docs/vault/CPI_2024_09_11_TIGHT_BASELINE.md](vault/CPI_2024_09_11_TIGHT_BASELINE.md)

Typical flow from historical MBO to backtest:

```
events.csv → Databento NPZ → run_event_replay.py (SignalBacktester) → research_cards/
                     ↳ optional: research_runner.py latency matrix (--skip-hft)
```

1. Configure `DATABENTO_API_KEY` in `.env`.
2. Download CPI probe: `python data_system/scripts/download_micro_probe.py` (or place NPZ under `data/npz/`).
3. Sync CHI404 latency: `bash scripts/chi404_sync_trial_data.sh` (or run probe on CHI404).
4. **Primary macro replay:**

```bash
python scripts/run_event_replay.py \
  --event-id CPI_2024_09_11_TIGHT \
  --chi404-summary runtime/latency_reports/latency_summary.json \
  --skip-hftbacktest
```

5. Hypothesis families: `features_engine/src/hypotheses/`, output under `research_cards/`.
6. Regime / market state: `features_engine/src/regime/`, `features_engine/src/pipeline/`.

Do **not** use `pipeline replay-sample` on trial NPZ for macro event research — see vault entrypoints §5.

Key invariant: **no lookahead** — all features must be computable from filtration \(F_t\) at event time. See [REVIEWER_CHARTER.md](REVIEWER_CHARTER.md) Pass B.

## 5. CHI404 production infra

CHI404 is the Chicago colo bare-metal host used for kernel tuning, jitter gates, and latency validation.

```bash
# From your workstation — sync repo to server
bash scripts/sync_chi404_repo.sh

# Remote tuning + validation (PowerShell on Windows)
.\scripts\run_chi404_tuning_remote.ps1
.\scripts\run_chi404_validate_remote.sh
```

Scripts live in `infrastructure/chi404/`. Pass criteria: `infrastructure/chi404/PASS_CRITERIA.json`. Validator: `validate_pass_criteria.py`.

Expected outcomes (validated RUN_ID `20260529T112136Z`):

- Cyclictest p99 on isolated CPUs: **≤ 20 µs** (measured ~11 µs)
- NIC ring size documented in `ring_buffer_limitation.json` when hardware caps at 511

Set `HFT3_RITHMIC_HOST` on the server for gateway RTT probes once Rithmic connectivity is configured.

## 6. Rithmic trial lane (interim)

Until R\|API SDK arrives, live capture uses R\|Trader Pro as a bridge. **This lane is quarantined** — it never writes to trusted Databento `data/npz/`.

```
R|Trader Pro → raw capture → normalize → validate → HftBacktest NPZ → replay sample
```

CHI404 (Paper Trading, Chicago — **only path for live capture**):

```bash
# On CHI404 only — see docs/rithmic_trial/README.md
export RITHMIC_TRIAL_ENABLED=1
python -m data_system.rithmic_trial.pipeline run-unattended \
  --config data_system/config/rithmic_trial.yaml
```

Fixture mode (no live broker):

```bash
python -m data_system.rithmic_trial.pipeline capture --config data_system/config/rithmic_trial.yaml
python -m data_system.rithmic_trial.pipeline process --date YYYY-MM-DD --symbol MES
```

Full spec: [rithmic_trial_hftbacktest_pipeline_prompt.pdf](../rithmic_trial_hftbacktest_pipeline_prompt.pdf).  
Operational detail: [docs/rithmic_trial/README.md](rithmic_trial/README.md).

## 7. Code navigation (graphify)

Before editing code, consult the knowledge graph:

```powershell
.\scripts\graphify_pre_edit.ps1
graphify query "where is the rithmic trial pipeline entry point?"
```

After any code edit, rebuild (mandatory, AST-only, no API key):

```powershell
.\scripts\graphify_rebuild.ps1
```

Full workflow: [GRAPHIFY_WORKFLOW.md](GRAPHIFY_WORKFLOW.md).

## 8. Agent and review workflow

Multi-step or multi-file work uses orchestrator + subagents:

```
Spec → GraphPre → Plan → Code → Review (Karpathy + math) → Verify → GraphPost
```

| Doc | Purpose |
|-----|---------|
| [AGENTS.md](../AGENTS.md) | Roles, Karpathy principles, hft3 constraints |
| [AGENTIC_ENGINEERING.md](AGENTIC_ENGINEERING.md) | Delegation table, verify commands |
| [REVIEWER_CHARTER.md](REVIEWER_CHARTER.md) | Dual-pass review: engineering + PDF math invariants |

Cursor rules in `.cursor/rules/` enforce this for AI-assisted development.

## 9. Repository layout

```
hft3/
├── BLUEPRINT.md                 # Developer handoff summary
├── AGENTS.md                    # Agent charter
├── data_system/                 # Ingestion, Databento, Rithmic trial
├── features_engine/             # MBO features, hypotheses, regime, C++ extractor
├── backtest_pipeline/           # NPZ conversion, replay, research runner
├── decision_engine/             # Feature store, targets, training
├── infrastructure/              # Kernel tuning; chi404/ for bare metal
├── telemetry/                   # Dashboards and latency reporting
├── scripts/                     # CHI404 sync, setup_rithmic_chi404.sh, graphify, offline pipeline
├── tests/                       # pytest suite
├── docs/                        # Operational guides (this file)
└── graphify-out/                # Committed knowledge graph artifacts
```

## 10. Verification checklist

| Check | Command |
|-------|---------|
| Unit tests | `python -m pytest tests/ -q` |
| CPI event replay | `python scripts/run_event_replay.py --event-id CPI_2024_09_11_TIGHT --skip-hftbacktest` |
| Event replay tests | `python -m pytest tests/test_run_event_replay.py -q` |
| Rithmic fixture pipeline | `python -m pytest tests/test_rithmic_trial_pipeline.py -q` |
| Rithmic topology guards | `python -m pytest tests/test_rithmic_topology_guards.py -q` |
| Graph fresh after edits | `.\scripts\graphify_rebuild.ps1` |
| CHI404 validate (remote) | `bash scripts/run_chi404_validate_remote.sh` |

## 11. What is not in git

- `.env` — credentials and API keys
- `data/**/*.npz`, `data/raw/rithmic_trial_live_capture/**` — market data
- `logs/`, `graphify-out/cache/` — local runtime artifacts
- `chi404_infra.tgz` — ops transfer bundles

When in doubt, check `.gitignore` before adding files.
