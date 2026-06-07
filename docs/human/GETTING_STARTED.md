# Getting Started ΓÇö hft3 end-to-end

Chicago CME microstructure research and execution stack. **Read this document once, top to bottom**, before editing code. For a printable checklist of every doc in order, see [DOC_INDEX.md](DOC_INDEX.md).

---

## 1. What this repo is

hft3 implements [BLUEPRINT.md](../BLUEPRINT.md): a filtration-safe, event-time MBO research stack with a separate **live colo path** (CHI404) and **offline / workstation** research paths.

| Lane | Purpose | Primary paths |
|------|---------|---------------|
| **Research ingest** | Databento MBO ΓåÆ NPZ | `data_system/` |
| **Features** | MBO features, hypotheses, regime | `features_engine/` |
| **Backtest** | HftBacktest replay, research runner | `backtest_pipeline/` |
| **Workbench** | Event-window backtests, campaigns, latency PASS | `workbench/`, `research_cards/` |
| **After-action** | Post-run packet, symbolic checks, local LLM narrative | `data_layer/` |
| **Decision** | Feature store, training | `decision_engine/python/` |
| **Production infra** | CHI404 tuning and validation | `infrastructure/chi404/` |
| **Rithmic trial** | Quarantined interim external capture | `data_system/rithmic_trial/` |
| **Telemetry** | Latency dashboards | `telemetry/` |

**Topology (non-negotiable):** external broker capture and orders run on **CHI404 bare metal** only. The dev workstation runs offline replay, pytest, workbench, and **post-run** after-action LLM ΓÇö never the external Rithmic hot loop. See BLUEPRINT ┬º4.

---

## 2. Authority documents

Mathematical and production rules live in PDFs ΓÇö not duplicated in prose here.

| Location | Use |
|----------|-----|
| [docs/references/](references/README.md) | **Canonical PDF bundle** for citations and reviewers |
| [docs/references/MANIFEST.md](references/MANIFEST.md) | Field ΓåÆ PDF section map (after-action packets) |
| Repo root `*.pdf` | Legacy links; prefer `docs/references/` copies |
| [BLUEPRINT.md](../BLUEPRINT.md) | Developer handoff summary |
| [REVIEWER_CHARTER.md](REVIEWER_CHARTER.md) | Pass A (engineering) + Pass B (math invariants) |

---

## 3. Prerequisites

- **Python 3.11+**, **Git**, **pip**
- **Optional:** CMake + C++17 for `features_engine/cpp/`
- **Optional:** `graphify` CLI ΓÇö `pip install graphifyy` (AST rebuild; no cloud API required)
- **Optional:** OpenAI-compatible GPT-5.5 endpoint for workbench after-action reports; local Ollama remains optional only for semantic graphify
- **CHI404:** SSH `Host chi404` in `~/.ssh/config`
- **Rithmic trial live:** CHI404 only ΓÇö see [rithmic_trial/README.md](rithmic_trial/README.md)

---

## 4. First-time setup

### 4.1 Clone with vendor submodules

```bash
git clone --recurse-submodules https://github.com/javin23863/hft3.git
cd hft3
```

If you already cloned without submodules:

```bash
git submodule update --init vendor/openfoundry vendor/alphageometry
```

| Submodule | Upstream |
|-----------|----------|
| `vendor/openfoundry/` | [syzygyhack/open-foundry](https://github.com/syzygyhack/open-foundry) |
| `vendor/alphageometry/` | [google-deepmind/alphageometry](https://github.com/google-deepmind/alphageometry) |

Pins: [integrations/openfoundry/VENDOR.lock](../integrations/openfoundry/VENDOR.lock)

### 4.2 Environment and dependencies

```bash
cp .env.example .env          # fill locally; never commit
pip install -r data_system/requirements.txt
pip install -r backtest_pipeline/requirements.txt
pip install -r workbench/requirements.txt
pip install -e .                    # pulls jsonschema for packet-strict LLM validation
pip install graphifyy         # code navigation (recommended)
pip install openai            # optional; only for graphify semantic local Ollama
```

### 4.3 Baseline verification

```bash
python -m pytest tests/ -q
```

Expect some skips when optional C++ golden binaries or CHI404 fixtures are absent ΓÇö see test output, not a silent PASS.

---

## 5. Research pipeline (offline)

**Entrypoints:** [vault/RESEARCH_ENTRYPOINTS.md](vault/RESEARCH_ENTRYPOINTS.md)  
**CPI baseline:** [vault/CPI_2024_09_11_TIGHT_BASELINE.md](vault/CPI_2024_09_11_TIGHT_BASELINE.md)

```
events.csv ΓåÆ Databento NPZ ΓåÆ run_event_replay.py ΓåÆ research_cards/
```

1. Copy `.env.example` to `.env` and set `DATABENTO_API_KEY` (see [vault/WORKSTATION_ONE_LANE.md](vault/WORKSTATION_ONE_LANE.md)).
2. Download missing NPZ via Databento event windows (not multi-year pulls):

```bash
python workbench/scripts/backfill_catalog.py --model SPREAD_BLOWOUT_RECOMPRESSION --symbol MES.v.0 --dry-run
python workbench/scripts/backfill_catalog.py --model SPREAD_BLOWOUT_RECOMPRESSION --symbol MES.v.0 --download-missing --max-cost-usd 25
```

3. Sync CHI404 latency summary when available: `bash scripts/chi404_sync_trial_data.sh`.
4. Primary macro replay:

```bash
python scripts/run_event_replay.py \
  --event-id CPI_2024_09_11_TIGHT \
  --chi404-summary runtime/latency_reports/latency_summary.json \
  --skip-hftbacktest
```

**Invariant:** no lookahead ΓÇö features use filtration \(F_t\) only. [REVIEWER_CHARTER.md](REVIEWER_CHARTER.md) Pass B.

Do **not** use trial NPZ for trusted macro research without quarantine checks ΓÇö see vault entrypoints.

---

## 6. Workbench lane

Event-window backtests with C++ latency authority, walk-forward campaigns, and Streamlit UI.

**Full guide:** [workbench/README.md](workbench/README.md)

```bash
python -m workbench list
python -m workbench run --model SPREAD_BLOWOUT_RECOMPRESSION --event-id CPI_2024_09_11_TIGHT --full-sweep
python run_workbench.py campaign --model SPREAD_BLOWOUT_RECOMPRESSION --symbol MES.v.0 --full-sweep
```

| Flag | Meaning |
|------|---------|
| `--full-sweep` | Full latency injection matrix + diagnostics (required for after-action) |
| default (fast sweep) | Skips heavy sweep and **skips after-action** |

Run outputs: `research_cards/workbench_runs/` (**gitignored** ΓÇö local only).

---

## 7. After-action reports (post-run only)

Runs **after** a full-sweep workbench event completes on the **workstation** (Windows/macOS by default). Not in the MBO hot path; not on CHI404 unless `HFT3_AFTER_ACTION=1`.

**Spec:** [workbench/AFTER_ACTION_REPORTS.md](workbench/AFTER_ACTION_REPORTS.md)

Pipeline:

```
diagnostics.json + trades.parquet → MicrostructureAARPacket → symbolic invariants → KG JSONL → GPT-5.5 (OpenAI-compatible, packet-strict)
```

Per-run artifacts (when enabled):

| File | Content |
|------|---------|
| `after_action_packet.json` | Structured packet (ns/┬╡s, PDF citations) |
| `after_action_symbolic.json` | Deterministic latency invariant pass/fail |
| `after_action_response.json` | Canonical LLM output (schema-validated) |
| `after_action_report.md` | Plain-English narrative (derived) |
| `after_action_meta.json` | `llm_status`, skip reasons, timing |

Setup:

1. Submodules initialized (┬º4.1).
2. GPT-5.5 endpoint configured with `HFT3_LLM_API_KEY` or `OPENAI_API_KEY` (`pip install -e .` is enough; the runtime uses stdlib HTTP).
3. Charter PDFs present in `docs/references/` (see MANIFEST).

```bash
pytest tests/test_data_layer/ -q -m "not slow"
```

Global KG append: `research_cards/kg/nodes.jsonl` (structure committed; run rows are local).

---

## 8. CHI404 production infra

Chicago colo bare metal ΓÇö kernel tuning, jitter gates, latency validation.

```bash
bash scripts/sync_chi404_repo.sh
.\scripts\run_chi404_tuning_remote.ps1    # Windows ΓåÆ remote
bash scripts/run_chi404_validate_remote.sh
```

Pass criteria: `infrastructure/chi404/PASS_CRITERIA.json`.

---

## 9. Rithmic trial lane (quarantined)

Interim external capture via R\|Trader until R\|API. **Never writes to trusted `data/npz/`.**

External capture: **CHI404 only.**

```bash
# CHI404 ΓÇö see docs/rithmic_trial/README.md
python -m data_system.rithmic_trial.pipeline run-unattended \
  --config data_system/config/rithmic_trial.yaml

# Fixture mode (workstation / CI)
python -m data_system.rithmic_trial.pipeline capture --config data_system/config/rithmic_trial.yaml
python -m data_system.rithmic_trial.pipeline process --date YYYY-MM-DD --symbol MES
```

Spec: [docs/references/rithmic_trial_hftbacktest_pipeline_prompt.pdf](references/rithmic_trial_hftbacktest_pipeline_prompt.pdf)

---

## 10. Code navigation (graphify)

| Task | Command | API key? |
|------|---------|----------|
| Before edits | `.\scripts\graphify_pre_edit.ps1` then `graphify query "..."` | No |
| After code edits | `.\scripts\graphify_rebuild.ps1` or `graphify update .` | No |
| Optional semantic (PDFs/docs) | `.\scripts\graphify_semantic_local.ps1` | No ΓÇö uses **local Ollama** |

Do **not** use Google/Gemini for routine rebuilds. Full workflow: [GRAPHIFY_WORKFLOW.md](GRAPHIFY_WORKFLOW.md).

---

## 11. Agent and review workflow

```
Spec ΓåÆ GraphPre ΓåÆ Plan ΓåÆ Code ΓåÆ Review (Karpathy + math) ΓåÆ Verify ΓåÆ GraphPost
```

| Doc | Purpose |
|-----|---------|
| [AGENTS.md](../AGENTS.md) | Roles, topology, constraints |
| [AGENTIC_ENGINEERING.md](AGENTIC_ENGINEERING.md) | Delegation and verify commands |
| [REVIEWER_CHARTER.md](REVIEWER_CHARTER.md) | Dual-pass review contract |

---

## 12. Repository layout

```
hft3/
Γö£ΓöÇΓöÇ BLUEPRINT.md                 # Spec summary
Γö£ΓöÇΓöÇ AGENTS.md                    # Agent charter
Γö£ΓöÇΓöÇ data_layer/                  # After-action: packet, symbolic, KG, GPT-5.5 runtime
Γö£ΓöÇΓöÇ integrations/openfoundry/    # hft3 CME MBO connector + VENDOR.lock
Γö£ΓöÇΓöÇ vendor/openfoundry/          # syzygyhack/open-foundry submodule
Γö£ΓöÇΓöÇ vendor/alphageometry/        # AlphaGeometry submodule (symbolic pattern ref)
Γö£ΓöÇΓöÇ data_system/                 # Databento, Rithmic trial
Γö£ΓöÇΓöÇ features_engine/             # MBO features, hypotheses, C++
Γö£ΓöÇΓöÇ backtest_pipeline/           # NPZ, replay, research runner
Γö£ΓöÇΓöÇ workbench/                   # Event backtests, campaigns, UI
Γö£ΓöÇΓöÇ decision_engine/             # Training lane
Γö£ΓöÇΓöÇ infrastructure/chi404/       # Bare-metal tuning
Γö£ΓöÇΓöÇ research_cards/
Γöé   Γö£ΓöÇΓöÇ kg/                      # File-backed KG (JSONL)
Γöé   ΓööΓöÇΓöÇ workbench_runs/          # Local run artifacts (gitignored)
Γö£ΓöÇΓöÇ tests/                       # pytest
Γö£ΓöÇΓöÇ docs/                        # Operational guides (this file)
ΓööΓöÇΓöÇ graphify-out/                # Committed code graph
```

---

## 13. Verification checklist

| Check | Command |
|-------|---------|
| Full unit suite | `python -m pytest tests/ -q` |
| Workbench + after-action | `pytest tests/test_workbench/ tests/test_data_layer/ -q -m "not slow"` |
| CPI event replay | `python scripts/run_event_replay.py --event-id CPI_2024_09_11_TIGHT --skip-hftbacktest` |
| Rithmic fixture pipeline | `pytest tests/test_rithmic_trial_pipeline.py -q` |
| Rithmic topology guards | `pytest tests/test_rithmic_topology_guards.py -q` |
| Graph after edits | `.\scripts\graphify_rebuild.ps1` |
| CHI404 validate (remote) | `bash scripts/run_chi404_validate_remote.sh` |
| Submodules present | `git submodule status` |

---

## 14. What is not in git

| Path | Reason |
|------|--------|
| `.env` | Secrets |
| `data/**/*.npz`, external Rithmic raw | Market data |
| `research_cards/workbench_runs/` | Ephemeral run outputs |
| `logs/`, `graphify-out/cache/` | Local runtime |
| `graphify-out/manifest.json`, `cost.json` | Local graphify metadata |

See `.gitignore` before adding files.

---

## 15. Where to go next

| Goal | Document |
|------|----------|
| Full doc reading order | [DOC_INDEX.md](DOC_INDEX.md) |
| Workbench deep dive | [workbench/README.md](workbench/README.md) |
| After-action setup | [workbench/AFTER_ACTION_REPORTS.md](workbench/AFTER_ACTION_REPORTS.md) |
| Open Foundry integration | [integrations/openfoundry/README.md](../integrations/openfoundry/README.md) |
