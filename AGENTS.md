# hft3 Agent Charter

Chicago CME microstructure research and execution stack. Agents working in this repo follow mandatory delegation, Karpathy engineering principles, and hft3-specific constraints below.

Full workflow reference: [docs/AGENTIC_ENGINEERING.md](docs/AGENTIC_ENGINEERING.md)

## Mandatory delegation

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
- Skip verification after code changes

Typical chain: investigator locates site → builder edits → reviewer audits diff → main thread verifies.

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
2. **GraphPre** — Before any code edit session: `scripts/graphify_pre_edit.ps1`, then `graphify query "..."` or read fresh `graphify-out/GRAPH_REPORT.md`. Prefer graph query over blind grep. See [docs/GRAPHIFY_WORKFLOW.md](docs/GRAPHIFY_WORKFLOW.md).
3. **Plan** — Brief plan with verification steps before editing. Delegate locate work when needed (with graph context).
4. **Code** — Minimal change via builder or approved multi-file path. No drive-by edits.
5. **Verify** — **cavecrew-reviewer** must complete Pass A (Karpathy) and Pass B (math invariants) on the diff before **shell** runs `pytest` and CHI404 validate gates when infra applies. Loop until met or blocked.
6. **GraphPost** — After code edits: `graphify update .` or `scripts/graphify_rebuild.ps1`. Commit updated `graphify-out/` with the change when the team tracks graph in git.

Do not skip GraphPre, Plan, Verify, or GraphPost for "small" changes.

## hft3-specific constraints

### CHI404 bare metal

Production tuning and validation run on CHI404 via SSH (`Host chi404` in `~/.ssh/config`).

- Sync repo: `bash scripts/sync_chi404_repo.sh`
- Remote tuning: `scripts/run_chi404_tuning_remote.ps1`, `infrastructure/chi404/`
- Launch session: `scripts/launch_chi404.ps1`

Do not assume local Windows paths apply on CHI404.

### Rithmic trial quarantined lane

Trial capture is isolated from trusted production data (`data/npz/` from Databento).

- Code: `data_system/rithmic_trial/`
- Config: `data_system/config/rithmic_trial.yaml`
- Docs: [docs/rithmic_trial/README.md](docs/rithmic_trial/README.md)

Do not write trial output into production NPZ paths or bypass quarantine without explicit approval.

### Secrets

Never commit credentials. Use `.env` locally (see `.env.example`). Do not add API keys, passwords, or private keys to git.

### rithmic_gateway C++ hot path

Do not modify `rithmic_gateway/` C++ execution hot path unless the user explicitly requests it. Trial Wine/R|Trader wiring stays in the Python trial lane, not the gateway hot path.
