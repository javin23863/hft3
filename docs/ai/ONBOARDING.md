# AI developer onboarding (graph-first)

**Fable first, Ponytail second, then graph, then prose.** Humans start at [docs/human/GETTING_STARTED.md](../human/GETTING_STARTED.md).

**Canonical repo:** [docs/REPO_STATE.md](../REPO_STATE.md) — path, `main` HEAD, branch cleanup, `git status` verification (read before assuming workspace path or branch).

## Step 0 — Fable mindset (mandatory, always first)

1. Read vault **Agent Runtime Roadmap** (`architecture/Agent Runtime Roadmap` in Obsidian) or repo [docs/vault/AGENT_RUNTIME_ROADMAP.md](../vault/AGENT_RUNTIME_ROADMAP.md) for the full ANY-LLM gate order.
2. Load [.cursor/rules/00-fable-mindset.mdc](../../.cursor/rules/00-fable-mindset.mdc) — hardened always-on Cursor runtime rule.
3. Read [docs/vault/FABLE_MINDSET.md](../vault/FABLE_MINDSET.md) — repo vault copy (latency vocabulary, hard rejects, read order).
4. Full reference when needed: `C:\Users\MSI\.codex\skills\fable-mindset\references\Fable_Mindset_public.md`.

**Blocking:** No VaultGate, graph query, search, script, or edit until Fable is loaded.

## Step 1 — Ponytail mindset (mandatory, second — before codebase)

1. Load [.cursor/rules/01-ponytail-mindset.mdc](../../.cursor/rules/01-ponytail-mindset.mdc) — second always-on Cursor runtime rule.
2. Read [docs/ai/PONYTAIL.md](PONYTAIL.md) — hft3 charter (ladder, not-lazy-about, Vast ops boundary).
3. Upstream repo: https://github.com/DietrichGebert/ponytail · Vendored: `vendor/ponytail/`

**Blocking:** No code or doc edits until Ponytail ladder is active.

## Step 2 — Vault ontology gate (after Fable + Ponytail)

1. Read vault `wiki/hot.md`, `Home.md`, `Memory Stack.md` (path: `C:\Users\MSI\Desktop\Obsidian Vault From VPS\hft3` or `$env:HFT3_VAULT_ROOT`).
2. Run task-specific consult:

```powershell
.\scripts\vault_gate.ps1 -Query "where does X fit in hft3 ontology?"
.\scripts\vault_pre_edit.ps1
```

3. Check `graph_gates` in `runtime/vault-gate/.last-vault-gate.json`. If `waived-by-owner-2026-06-16`, skip Step 1 graph below.

## Step 3 — Code graph (when not owner-waived)

1. Open [graphify-out/wiki/index.md](../../graphify-out/wiki/index.md) — check **Freshness** banner (timestamp + git SHA).
2. Run scoped queries (do not read raw `GRAPH_REPORT.md` or full `graph.json`):

```bash
graphify query "where is X defined?"
graphify explain ReplaySession
graphify path run_event_replay build_certification_stamp
```

3. Before any edit: `scripts/graphify_pre_edit.ps1` or confirm `graphify-out/graph.json` exists.
4. After code edits: `graphify update .` or `scripts/graphify_rebuild.ps1`.

Regenerate wiki index:

```bash
python tools/graphify/build_wiki_index.py
```

## Step 4 — Agent charter

- [AGENTS.md](../../AGENTS.md) — delegation, topology (CHI404 only for live), verify loop
- [ENGINEERING.md](ENGINEERING.md) — Karpathy principles (canonical coding style)
- [SHELL_EXECUTION.md](SHELL_EXECUTION.md) — **time-bounded shell/SSH/pytest (mandatory)**
- [docs/AGENTIC_ENGINEERING.md](../AGENTIC_ENGINEERING.md) — Spec -> GraphPre -> Plan -> Code -> GrepLoop -> Review -> Verify -> GraphPost
- [GREPLOOP.md](GREPLOOP.md) — mandatory `rg` loop for stale terms, old fields, missing evidence rows, and external PR AI review when available
- [docs/project/PROJECT_PLANNING_STANDARD.md](../project/PROJECT_PLANNING_STANDARD.md) — literature-traceable feature control before roadmap changes
- [docs/project/FEATURE_LITERATURE_TRACEABILITY_MATRIX.md](../project/FEATURE_LITERATURE_TRACEABILITY_MATRIX.md) — feature classification and acceptance basis

## Step 5 — Math invariants

- [BLUEPRINT.md](../../BLUEPRINT.md)
- [docs/REVIEWER_CHARTER.md](../REVIEWER_CHARTER.md) — Pass A + Pass B with PDF citations

## Step 6 — Operating the system (when needed)

Only after graph + charter: [docs/human/DOC_INDEX.md](../human/DOC_INDEX.md).

**CHI404 hardware/runtime (before infra edits):** [docs/ai/chi404_system_spec.json](chi404_system_spec.json) · human: [docs/chi404/HARDWARE_BASELINE.md](../chi404/HARDWARE_BASELINE.md)

## Step 7 — Verification

**Honest status:** Every handoff uses the block in [docs/VALIDATION_HONESTY.md](../VALIDATION_HONESTY.md). Subset pytest is not scope-green.

**Time budgets are mandatory:** [SHELL_EXECUTION.md](SHELL_EXECUTION.md)

| Gate | Command | Hard stop |
|------|---------|-----------|
| Agent verify (preferred) | `powershell -File scripts/run_agent_verify.ps1` or `bash scripts/run_agent_verify.sh` | 180s |
| T0 backtester | `python -m pytest tests/backtester_validation/fast -q` | 90s |
| Full pytest | `python -m pytest tests/ -q` | 600s (explicit request only) |
| Certification status | `bash scripts/check_backtester_certification_status.sh` | 60s |

Use `tools/shell/run_with_timeout.ps1` (Windows) or `run_with_timeout.sh` (Unix) for other long commands.

## Topology reminder

Live/paper Rithmic capture and orders: **CHI404 bare metal only**. Workstation: offline replay, pytest, workbench, after-action LLM — never the hot loop.
