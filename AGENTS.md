# hft3 Agent Charter

Chicago CME microstructure research and execution stack. Agents working in this repo follow mandatory delegation, Karpathy engineering principles, and hft3-specific constraints below.

**Known gaps (mandatory read):** [KNOWN_GAPS.md](KNOWN_GAPS.md) — data lanes, CHI404 vs workstation, imbalance/crypto/equities holes, do-not-trust list.

**Human onboarding:** [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) (read once, top to bottom) · [docs/human/DOC_INDEX.md](docs/human/DOC_INDEX.md) · [docs/ai/ONBOARDING.md](docs/ai/ONBOARDING.md) (graph-first for agents) · [docs/ai/ENGINEERING.md](docs/ai/ENGINEERING.md) (Karpathy style)

Full workflow reference: [docs/AGENTIC_ENGINEERING.md](docs/AGENTIC_ENGINEERING.md)

## Mandatory delegation

**If you are about to edit code without spawning investigator → builder → reviewer → shell, STOP.**

The main agent orchestrates. It does not absorb large exploration or multi-file edits inline.

| Task | Delegate to |
|------|-------------|
| Locate definitions, callers, usages | `cavecrew-investigator` |
| Surgical edit, at most 2 files, scope obvious | `cavecrew-builder` |
| Diff, branch, or file audit (dual-pass) | `cavecrew-reviewer` per [docs/REVIEWER_CHARTER.md](docs/REVIEWER_CHARTER.md) — Pass A (Karpathy) + Pass B (PDF math invariants) |
| Broad read-only codebase survey | `explore` |
| Shell, git, deploy, remote commands | `shell` |

Main thread responsibilities: clarify the goal, choose delegation, merge subagent receipts, verify outcomes, and decide next step.

**Parallel investigators OK** — e.g. defs vs callers vs tests in one message; aggregate in main thread.

Main thread must **not**:

- Run large inline grep/read sweeps when investigator or explore would suffice
- Edit three or more files in one turn without explicit user approval
- Skip subagent delegation and absorb locate → edit → review inline to save time
- Wire a dev workstation into live/paper Rithmic or execution paths (BLUEPRINT §4 violation)
- Skip verification after code changes
- Claim merge-ready or "done" without a reviewer verdict and green verify commands
- Hide skipped tests or missing tooling behind a passing pytest summary

Typical chain: investigator locates site → builder edits → reviewer audits diff → shell verifies → graph post.

## Trust: non-skippable workflow

This stack drives real research and execution. **Skipping delegation or verification is never acceptable**, even for "small" or urgent fixes. The user must be able to trust that every change ran the full chain.

### Required subagent chain (every code change)

Run this loop in order. The main thread **orchestrates and integrates**; it does **not** substitute for subagents.

1. **Locate** — `cavecrew-investigator` or `explore` when definitions, callers, or test sites are not already known.
2. **Edit** — `cavecrew-builder` for surgical changes (≤2 files). Multi-file work stays in main/feature agent with explicit user approval per batch.
3. **Review** — `cavecrew-reviewer` dual-pass (Karpathy + math invariants) **before** claiming the change is sound. Report reviewer receipt: 🔴 count, 🟡 count, **merge-ready yes/no**.
4. **Verify** — `shell` runs `pytest` (and CHI404 validate when infra applies). Paste or summarize command output; do not narrate "tests pass" without evidence.
5. **GraphPost** — `graphify update .` or `scripts/graphify_rebuild.ps1` after edits. Commit updated `graphify-out/` with the change when the team tracks graph in git.

**Parallel investigators** are OK. **Skipping any step** is not.

### If the chain was skipped

Stop. Acknowledge the miss to the user. Re-run the full **Spec → GraphPre → Plan → Delegate → Verify → GraphPost** loop on the current diff before more edits or a merge/commit narrative.

### Merge-ready criteria (honest status)

Do not tell the user work is merge-ready unless **all** of the following are true:

| Gate | Requirement |
|------|-------------|
| Reviewer | `cavecrew-reviewer` verdict **merge-ready: yes**, **0 🔴** |
| Tests | Scope-green per [docs/VALIDATION_HONESTY.md](docs/VALIDATION_HONESTY.md): full scope pytest or gate script with **exit code and output tail** pasted in thread — not targeted file subsets alone. Full-repo `pytest` when scope is ambiguous. |
| Skipped tests | Every skip has a **documented blocker** (e.g. CMake missing → `test_cpp_feature_golden` skipped). Say **merge-ready: no** until the gate runs or the user explicitly accepts the skip. |
| C++ parity | When Python/C++ feature slots change: build `hft_feature_golden` and pass `tests/test_cpp_feature_golden.py` |
| Graph | `graphify-out/` rebuilt after code edits when graph is tracked in git |

When blocked, state **what ran**, **what was skipped**, and **what unblocks** — do not imply completion.

### Honest completion (no verification theater)

- **Subset pytest is not scope-green.** A targeted pass (e.g. 10/10 on one file) while the scope test directory fails does not satisfy the Tests gate.
- **User-waived verify is not done.** If the user says "don't test" or "code only", report `verify-run: WAIVED (user)` and **`merge-ready: no`**. Verify-gated plan todos stay **`pending`** or **`waived-not-verified`** — never **`completed`**.
- **Plan todo theater is forbidden.** Frontmatter `status: completed` on verify todos requires pasted green output from the verify command, or an explicit user acceptance of waiver in the thread.
- **All handoffs** must include the status block in [docs/VALIDATION_HONESTY.md](docs/VALIDATION_HONESTY.md) (`merge-ready`, `scope-green`, `scope`, `verify-run`, `data-mode`, `known-gaps`). Lane addenda (e.g. crypto PIT gaps) supplement but do not replace the repo-wide charter.

## Shell execution (time-bounded — mandatory)

Background pytest, SSH wait loops, and subprocess jobs **must not run unbounded**. Hung work wastes resources and hides failures.

**Full policy:** [docs/ai/SHELL_EXECUTION.md](docs/ai/SHELL_EXECUTION.md) · Cursor rule: [.cursor/rules/shell-execution-timeouts.mdc](.cursor/rules/shell-execution-timeouts.mdc)

| Rule | Requirement |
|------|-------------|
| Budget | Every command: expected duration + **hard stop** before run |
| Hung | **> 2× expected** with no output for 60s → **kill** and report BLOCKED |
| Orphans | Never leave pytest / `replay-sample` / SSH / Streamlit running after abort |
| Verify | Prefer `scripts/run_agent_verify.ps1` (180s cap) over background full-suite pytest |
| SSH | `-o ConnectTimeout=15`; no infinite `while ! grep …` loops from workstation |

Main thread and **shell** subagent: paste **exit code + summary line**; do not poll background shells indefinitely.

## Karpathy principles

Derived from standard Karpathy CLAUDE.md guidelines. Every task applies all four.

### 1. Think Before Coding

State assumptions explicitly. Present multiple interpretations when they exist; do not silently pick one. Surface tradeoffs. Stop and ask when confused rather than guessing.

### 2. Simplicity First

Minimum code that solves the problem. No speculative abstractions, extra configuration, or features beyond the request. If a senior engineer would call it overcomplicated, simplify.

### 3. Surgical Changes

Touch only what the task requires. Match existing naming, types, and style. Every changed line traces to the request. Do not refactor unrelated code; mention dead code if noticed, do not delete it unless asked.

### 4. Goal-Driven Execution

Convert imperative instructions into verifiable success criteria. Prefer "write a failing test, then make it pass" over "fix the bug." Strong criteria let the agent loop independently; weak criteria ("make it work") require constant clarification.

## Spec → GraphPre → Plan → Code → Verify → GraphPost

Every task runs this loop:

1. **Spec** — Restate goal, constraints, and success criteria. Ask if ambiguous.
2. **GraphGate** — **Blocking:** `scripts/graphify_gate.ps1 -Query "..."` (or `bash scripts/graphify_gate.sh '...'`). Writes `graphify-out/.last-graph-query.json`. **No code edits before this.**
3. **GraphPre** — `scripts/graphify_pre_edit.ps1` (exits 2 if gate stamp missing/stale). Use graph query output — not blind repo grep. CHI404: [docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md](docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md).
4. **Plan** — Brief plan with verification steps before editing. Delegate locate work when needed (with graph context).
5. **Code** — Minimal change via builder or approved multi-file path. No drive-by edits. No parallel CHI404 orchestrators.
6. **Verify** — **cavecrew-reviewer** must complete Pass A (Karpathy) and Pass B (math invariants) on the diff before **shell** runs bounded pytest (see [docs/ai/SHELL_EXECUTION.md](docs/ai/SHELL_EXECUTION.md)) and CHI404 validate gates when infra applies. Loop until met or blocked.
7. **GraphPost** — After code edits: `graphify update .` or `scripts/graphify_rebuild.ps1`. Commit updated `graphify-out/` with the change when the team tracks graph in git.

Do not skip GraphGate, GraphPre, Plan, Verify, or GraphPost for "small" changes.

## hft3-specific constraints

### Research entrypoints (canonical order)

Macro backtest and replay: [docs/vault/RESEARCH_ENTRYPOINTS.md](docs/vault/RESEARCH_ENTRYPOINTS.md).  
Primary path: `scripts/run_event_replay.py` + `events.csv` + CHI404 `latency_summary.json`.  
Do not use `pipeline replay-sample` or trial NPZ paths for CPI/macro research.

### CHI404 bare metal

Production tuning and validation run on CHI404 via SSH (`Host chi404` in `~/.ssh/config`).

- Sync repo: `bash scripts/sync_chi404_repo.sh`
- Remote tuning: `scripts/run_chi404_tuning_remote.ps1`, `infrastructure/chi404/`
- Launch session: `scripts/launch_chi404.ps1`

Do not assume local Windows paths apply on CHI404.

### Topology: Chicago colo only (BLUEPRINT §4)

Per [BLUEPRINT.md §4 Live Architecture](BLUEPRINT.md#4-live-architecture): production live path is **CHI404 bare metal** with a **dedicated route to Rithmic Chicago/Aurora**. Milliseconds matter — nothing else belongs in the hot loop.

| Host | Role |
|------|------|
| **CHI404** | Live/paper market data, order submit, capture, tuning, PASS gates, Rithmic trial lane |
| **Dev workstation** | Offline research (Databento replay), pytest, git, SSH/sync to CHI404, docs — **never** live capture or orders |

**Forbidden** (unless the user explicitly requests an exception in the task):

- Windows or Mac R\|Trader capture, unattended daemons, or scheduled tasks on a dev PC
- File-bridge or API loop that routes live/paper data or orders through a non-colo host
- Setup scripts that auto-start capture on the workstation
- Treating workstation RTT as an operational dependency for execution or capture

**Before any Rithmic trial, infra, or latency work:** read BLUEPRINT §4 and [docs/rithmic_trial/README.md](docs/rithmic_trial/README.md). Live capture code must refuse to run on Windows (see `data_system/rithmic_trial/pipeline.py`).

### PDF structural models (signal layer)

Seven PDF structural slugs (e.g. `DEALER_HEDGING`, `HYBRID_EXECUTION`) from [Algorithmic Trading Strategy Development](docs/references/algorithmic_trading_strategy_development.pdf) are **not** hypothesis slugs:

- Code: `features_engine/src/structural_models/`
- Registry: [`packages/features_engine/config/model_registry.yaml`](packages/features_engine/config/model_registry.yaml) — 44 hypothesis + 7 PDF structural = **51 workbench slugs** (`legacy_id` `HYP_*` / `PDF_MODEL_*` deprecated)
- Docs: [docs/structural_models/PDF_MODELS.md](docs/structural_models/PDF_MODELS.md)

Do not merge PDF outputs into 64-dim `FeatureIndex` or `CombinedHypothesisStrategy` without C++ parity review.

### Microstructure workbench

Unified 51-slug research workbench (`workbench/`):

- CLI: `python -m workbench run --model SPREAD_BLOWOUT_RECOMPRESSION --event-id <from events.csv>` (both required; slugs: `python -m workbench list`; events: `python packages/data_system/src/macro_event_cli.py`)
- UI: `streamlit run workbench/ui/app.py`
- Artifacts: `research_cards/workbench_runs/<run_id>/`

See [docs/vault/RESEARCH_ENTRYPOINTS.md](docs/vault/RESEARCH_ENTRYPOINTS.md) section 6.

**Latency:** C++ hot-path distributions are the production source of truth; Python runtime is informational only. See [docs/workbench/LATENCY_ARCHITECTURE.md](docs/workbench/LATENCY_ARCHITECTURE.md).

### Rithmic trial quarantined lane

Trial capture is isolated from trusted production data (`data/npz/` from Databento).

- Code: `data_system/rithmic_trial/`
- Config: `data_system/config/rithmic_trial.yaml`
- Docs: [docs/rithmic_trial/README.md](docs/rithmic_trial/README.md)
- **CHI404 canonical paths (agents):** [docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md](docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md) — deploy via `chi404_vm_deploy.sh`; paper latency via live gate + real R|Trader exports only. **Never** host-side synthetic order log inject.

Do not write trial output into production NPZ paths or bypass quarantine without explicit approval.

### Options parity lane (operational, data-isolated)

Options parity research is operational. Data is isolated from trusted production CME paths (`data/npz/` from Databento) but the lane is not blocked from running.

- Code: `options_lane/`, `packages/equities_lane/src/options/`
- Data: `data/options/`, `data/replay/parity/`, `research_cards/parity/`
- Config: `options_lane/config/parity_universe.yaml`
- Source-of-truth: [docs/research/EQUITY_OPTIONS_DATA_MAP.md](docs/research/EQUITY_OPTIONS_DATA_MAP.md)

Data isolation invariant: options raw/normalized data lives under `data/options/`. Do not write options raw into production CME NPZ paths. Stock + option + combo + no-trade route decisions are produced for every decision; see `PLAN_EQOPT.md`.

### Low-float equities lane (operational, data-isolated)

Low-float momentum research is operational. Data is isolated from trusted production CME paths (`data/npz/`) but the lane is not blocked from running.

- Code: `packages/equities_lane/`
- Data: `data/equities/`, `data/replay/equities/`, `research_cards/equities/`, `data/options/equity_chains/` (OPRA)
- Config: `packages/equities_lane/config/universe.yaml`
- Docs: [docs/research/LOW_FLOAT_RUNNER.md](docs/research/LOW_FLOAT_RUNNER.md), [docs/research/EQUITY_OPTIONS_DATA_MAP.md](docs/research/EQUITY_OPTIONS_DATA_MAP.md)

Data isolation invariant: equities NPZ writes to `data/equities/npz/` only. L3-only enforcement (no silent MBP-1/MBP-10/bars downgrade in production research). Point-in-time filtration enforced for both equity and option data; contaminated runs are rejected before model fit/scoring with an explicit reason. Stock / option / stock+option / no-trade route comparison is required per [PLAN_EQOPT.md](PLAN_EQOPT.md).

**Data coverage:** All 13 decadal sessions have equity data (MBO L3). Option data (OPRA cbbo-1m) is available for 3/13 sessions (EXPR, GME, KODK); the other 10 sessions have empty option directories and will route to STOCK_ONLY. Feature-based EV estimation uses real `compute_features` pipeline (OFI, VPIN, Hawkes, HMM, option Greeks) with sampling for efficiency.

### Secrets

Never commit credentials. Use `.env` locally (see `.env.example`). Do not add API keys, passwords, or private keys to git.

### rithmic_gateway C++ hot path

Do not modify `rithmic_gateway/` C++ execution hot path unless the user explicitly requests it. Trial Wine/R|Trader wiring stays in the Python trial lane, not the gateway hot path.
