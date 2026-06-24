# MANDATORY FABLE MINDSET (FIRST — before every other gate): Load [docs/vault/FABLE_MINDSET.md](docs/vault/FABLE_MINDSET.md) and operate from the Fable loop before vault, graph, search, scripts, or edits. Cursor always-on rule: `.cursor/rules/00-fable-mindset.mdc`. Obsidian vault roadmap (ANY LLM): `architecture/Agent Runtime Roadmap` in `C:\Users\MSI\Desktop\Obsidian Vault From VPS\hft3\`. Full reference: `C:\Users\MSI\.codex\skills\fable-mindset\references\Fable_Mindset_public.md`. Ground → reason (name clock/metric/authority) → act in batches → observe → re-evaluate → read exact regions → verify with real checks → recover → report truthfully.
# MANDATORY PONYTAIL (SECOND — before touching codebase): Load `.cursor/rules/01-ponytail-mindset.mdc` immediately after Fable. Apply the lazy-senior-dev ladder on every code/doc edit — YAGNI, stdlib-first, minimal diffs, deletion over addition; never cut validation, security, or finance/math invariants. Upstream: https://github.com/DietrichGebert/ponytail · Vendored: `vendor/ponytail/` · Charter: [docs/ai/PONYTAIL.md](docs/ai/PONYTAIL.md). Vast/SSH ops use `vastai` + `scripts/vast_ssh_run_vbt_paid_screen.sh`, not ponytail.
# MANDATORY ONTOLOGY GATE (third, after Fable + Ponytail): Operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent codebases, pipelines, models, or methodology outside that authority.
# CANONICAL WORKING TREE: `C:\Users\MSI\repos\hft3`. Legacy stubs or alternate clones must redirect here; do not treat `C:\Users\MSI\Documents\hft3` or other hft3-looking paths as the active repo without an explicit migration plan.

# hft3 Agent Charter

Chicago CME microstructure research and execution stack. Agents working in this repo follow mandatory delegation, Karpathy engineering principles, and hft3-specific constraints below.

**Human onboarding:** [docs/REPO_STATE.md](docs/REPO_STATE.md) (canonical path + branch truth) · [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) (read once, top to bottom) · [docs/human/DOC_INDEX.md](docs/human/DOC_INDEX.md) · [docs/ai/ONBOARDING.md](docs/ai/ONBOARDING.md) (graph-first for agents) · [docs/ai/ENGINEERING.md](docs/ai/ENGINEERING.md) (Karpathy style)

Full workflow reference: [docs/AGENTIC_ENGINEERING.md](docs/AGENTIC_ENGINEERING.md)

## VaultGate: check project memory first

Before starting work, locating code, designing a change, or asking the user for missing context, consult the hft3 Obsidian vault (after Fable + Ponytail):

`C:\Users\MSI\Desktop\Obsidian Vault From VPS\hft3\`

**ANY LLM session start:** read vault `architecture/Agent Runtime Roadmap` or `wiki/hot.md` (Agent runtime roadmap section) for the full gate order.

Minimum read path:

1. `wiki/hot.md` for current state, blockers, moved lanes, and urgent handoffs.
2. `Home.md` for the curated KB map.
3. `Memory Stack.md` for the graph + vault protocol.
4. Relevant notes in `decisions/`, `sessions/`, `architecture/`, `pipelines/`, `validation/`, `operations/`, `references/`, or `library/`.

Use targeted search when the task has clear keywords, for example:

```powershell
rg -n "<task keywords>" "C:\Users\MSI\Desktop\Obsidian Vault From VPS\hft3" -g "*.md"
```

If the vault answers the question, proceed from that context and cite the note names in your reasoning or handoff. If it does not, state what vault notes/searches were checked before asking the user. VaultGate complements GraphGate: vault first for declarative memory, then graph first for code structure.

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
3. **Local preflight** — run the mandatory bounded task-specific `rg` loop in [docs/ai/GREPLOOP.md](docs/ai/GREPLOOP.md) to catch stale terms, old fields, missing required vocabulary, missing citation rows, and whitespace errors before reviewer time. Codex self-review is not a substitute.
4. **Review** — `cavecrew-reviewer` dual-pass (Karpathy + math invariants) **before** claiming the change is sound. Report reviewer receipt: 🔴 count, 🟡 count, **merge-ready yes/no**.
5. **Verify** — `shell` runs `pytest` (and CHI404 validate when infra applies). Paste or summarize command output; do not narrate "tests pass" without evidence.
6. **Plan Drift Review** — compare the executed diff/artifacts/receipts against the approved plan before Review Surface Gate.
7. **GraphPost** — only when graph gates are active: `graphify update .` or `scripts/graphify_rebuild.ps1` after edits. While graph gates are owner-waived, record the waiver and do not claim graph freshness.

**Parallel investigators** are OK. **Skipping any step** is not.

### If the chain was skipped

Stop. Acknowledge the miss to the user. Re-run the full **Spec → GraphPre when active → Plan → Delegate → Local Preflight → Review → Verify → Plan Drift → Review Surface → PR GrepLoop → GraphPost when active** loop on the current diff before more edits or a merge/commit narrative.

### Merge-ready criteria (honest status)

Do not tell the user work is merge-ready unless **all** of the following are true:

| Gate | Requirement |
|------|-------------|
| Reviewer | `cavecrew-reviewer` verdict **merge-ready: yes**, **0 🔴** |
| Local preflight / PR GrepLoop | Local preflight ran on the changed scope. After Plan Drift Review passes, create or reuse a PR/MR/CL review surface before GrepLoop; `unavailable(no-pr)` is a blocker, and an owner waiver must be recorded as `pr-ai-review: waived-by-user` plus `review-surface: none(waived-by-user: <reason>)`. |
| Tests | Scope-green per [docs/VALIDATION_HONESTY.md](docs/VALIDATION_HONESTY.md): full scope pytest or gate script with **exit code and output tail** pasted in thread — not targeted file subsets alone. Full-repo `pytest` when scope is ambiguous. |
| Skipped tests | Every skip has a **documented blocker** (e.g. CMake missing → `test_cpp_feature_golden` skipped). Say **merge-ready: no** until the gate runs or the user explicitly accepts the skip. |
| C++ parity | When Python/C++ feature slots change: build `hft_feature_golden` and pass `tests/test_cpp_feature_golden.py` |
| Graph | When graph gates are active, `graphify-out/` rebuilt after code edits when graph is tracked in git. While owner-waived, report `graph-gate: waived-by-owner` and do not claim graph freshness. |

When blocked, state **what ran**, **what was skipped**, and **what unblocks** — do not imply completion.

### Honest completion (no verification theater)

- **Subset pytest is not scope-green.** A targeted pass (e.g. 10/10 on one file) while the scope test directory fails does not satisfy the Tests gate.
- **User-waived verify is not done.** If the user says "don't test" or "code only", report `verify-run: WAIVED (user)` and **`merge-ready: no`**. Verify-gated plan todos stay **`pending`** or **`waived-not-verified`** — never **`completed`**.
- **Plan todo theater is forbidden.** Frontmatter `status: completed` on verify todos requires pasted green output from the verify command, or an explicit user acceptance of waiver in the thread.
- **All handoffs** must include the status block in [docs/VALIDATION_HONESTY.md](docs/VALIDATION_HONESTY.md) (`merge-ready`, `scope-green`, `scope`, `verify-run`, `plan-drift`, `data-mode`, `pr-ai-review`, `review-surface`, `known-gaps`). Lane addenda (e.g. options-lane PIT gaps) supplement but do not replace the repo-wide charter.

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

## Fable → Ponytail → VaultGate → Spec → GraphPre When Active → Plan → Code → Local Preflight → Review → Verify → Plan Drift → Review Surface → PR GrepLoop → GraphPost When Active

Every task runs this loop:

0. **Fable mindset** — **Blocking first:** `.cursor/rules/00-fable-mindset.mdc` + [docs/vault/FABLE_MINDSET.md](docs/vault/FABLE_MINDSET.md). Ground → reason → act → observe → re-evaluate before any gate or tool use.
1. **Ponytail mindset** — **Blocking second:** `.cursor/rules/01-ponytail-mindset.mdc` + [docs/ai/PONYTAIL.md](docs/ai/PONYTAIL.md). YAGNI ladder before any codebase touch.
2. **VaultGate** — **Blocking:** `scripts/vault_gate.ps1 -Query "..."` then `scripts/vault_pre_edit.ps1` (exits 2 if stamp missing/stale). Read/search Obsidian vault: `wiki/hot.md`, `Home.md`, `Memory Stack.md`, task-relevant `decisions/`/`sessions/`. Stamp: `runtime/vault-gate/.last-vault-gate.json`.
3. **Spec** — Restate goal, constraints, and success criteria. Ask only after VaultGate if ambiguity remains.
4. **GraphGate** — When **not** owner-waived (`wiki/hot.md` → `waived-by-owner-2026-06-16`): `scripts/graphify_gate.ps1 -Query "..."`. When waived: skip GraphGate/GraphPre/GraphPost; use VaultGate + targeted source reads.
5. **GraphPre** — Only when graph gates active: `scripts/graphify_pre_edit.ps1`.
6. **Plan** — Brief plan with verification steps before editing. Delegate locate work when needed (with graph context).
7. **Code** — Minimal change via builder or approved multi-file path. No drive-by edits. No parallel CHI404 orchestrators.
8. **Local preflight** — Before reviewer, run a bounded, task-specific `rg` loop for forbidden legacy terms, old fields, missing required terms/citation rows, and whitespace errors. Patch actionable hits; max three local iterations; report blockers instead of widening blindly.
9. **Review** — **cavecrew-reviewer** must complete Pass A (Karpathy) and Pass B (math invariants) on the diff before test commands can be used as merge evidence.
10. **Verify** — **shell** runs bounded pytest (see [docs/ai/SHELL_EXECUTION.md](docs/ai/SHELL_EXECUTION.md)) and CHI404 validate gates when infra applies. Loop until met or blocked.
11. **Plan Drift Review** — Compare the executed work against the approved plan before Review Surface Gate. If drift is found, fix it or update the approved plan, then rerun affected local gates.
12. **Review Surface Gate** — If no PR/MR/CL exists and the work is intended to advance toward merge-ready, create or reuse a branch plus PR/MR/CL review surface after Plan Drift Review passes. If publishing is blocked, report `pr-ai-review: unavailable(no-pr)` and `merge-ready: no`; if the owner waives the gate, report `pr-ai-review: waived-by-user` plus `review-surface: none(waived-by-user: <reason>)`.
13. **PR GrepLoop** — Run the installed external PR AI review loop on the current-head review surface; fix actionable feedback and rerun local gates before triggering it again. If the connector is missing or unauthenticated, report the blocker and `merge-ready: no`.
14. **GraphPost** — Only when graph gates are active: after code edits, run `graphify update .` or `scripts/graphify_rebuild.ps1`. While graph gates are owner-waived, skip GraphPost and report the waiver.

Do not skip Fable, Ponytail, VaultGate, VaultPre, GraphGate (when active), GraphPre, Plan, Local Preflight, Review, Verify, Plan Drift Review, Review Surface Gate, PR GrepLoop when available, or GraphPost (when active) for "small" changes.

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

### Topology: lane-scoped live hosts (BLUEPRINT §4)

Per [BLUEPRINT.md §4 Live Architecture](BLUEPRINT.md#4-live-architecture): the CME production live path is **CHI404 bare metal** with a **dedicated route to Rithmic Chicago/Aurora**. Milliseconds matter — nothing else belongs in the hot loop.

| Host | Role |
|------|------|
| **CHI404** | CME lane: live/paper market data, order submit, capture, tuning, PASS gates, Rithmic trial lane |
| **Dev workstation** | Offline research (Databento replay), pytest, git, SSH/sync, docs — **never** live capture or orders for any lane |

**Forbidden** (unless the user explicitly requests an exception in the task):

- Windows or Mac R\|Trader capture, unattended daemons, or scheduled tasks on a dev PC
- File-bridge or API loop that routes live/paper data or orders through a host outside the lane's designated live host
- Setup scripts that auto-start capture on the workstation
- Treating workstation RTT as an operational dependency for execution or capture

**Before any Rithmic trial, infra, or latency work:** read BLUEPRINT §4 and [docs/rithmic_trial/README.md](docs/rithmic_trial/README.md). Live capture code must refuse to run on Windows (see `data_system/rithmic_trial/pipeline.py`).

### PDF structural models (signal layer)

Seven models from [Algorithmic Trading Strategy Development](docs/references/algorithmic_trading_strategy_development.pdf) are **not** HYP families:

- Code: `features_engine/src/structural_models/`
- Registry: `get_structural_models()` — separate from `get_active_hypotheses()` (44 HYP + 7 PDF = 51 inventory)
- Docs: [docs/structural_models/PDF_MODELS.md](docs/structural_models/PDF_MODELS.md)

Do not merge PDF outputs into 64-dim `FeatureIndex` or `CombinedHypothesisStrategy` without C++ parity review.

### Microstructure workbench

Unified 51-model research workbench (`workbench/`):

- CLI: `python -m workbench run --model HYP_5 --event-id CPI_2024_09_11_TIGHT`
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

### Options parity lane (quarantined)

Options parity research is isolated from trusted production data (`data/npz/` from Databento).

- Code: `options_lane/`
- Data: `data/options/`, `data/replay/parity/`, `research_cards/parity/`
- Config: `options_lane/config/parity_universe.yaml`

Do not write options raw into production NPZ paths or bypass quarantine without explicit approval.

### Crypto and equities lanes (moved out)

The crypto lane and the low-float equities lane moved to the **hft3-crypto-lane** and **hft3-equities-lane** repos (split tag `pre-lane-split-20260612`). `packages/options_lane/` (CME futures options) remains in this repo.

### Secrets

Never commit credentials. Use `.env` locally (see `.env.example`). Do not add API keys, passwords, or private keys to git.

### rithmic_gateway C++ hot path

Do not modify `rithmic_gateway/` C++ execution hot path unless the user explicitly requests it. Trial Wine/R|Trader wiring stays in the Python trial lane, not the gateway hot path.
