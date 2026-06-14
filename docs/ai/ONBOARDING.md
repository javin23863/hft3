# AI developer onboarding (graph-first)

**Read the graph before prose.** Humans start at [docs/human/GETTING_STARTED.md](../human/GETTING_STARTED.md).

## Step 0 — Code graph (mandatory)

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

## Step 1 — Agent charter

- [AGENTS.md](../../AGENTS.md) — delegation, topology (CHI404 only for live), verify loop
- [ENGINEERING.md](ENGINEERING.md) — Karpathy principles (canonical coding style)
- [SHELL_EXECUTION.md](SHELL_EXECUTION.md) — **time-bounded shell/SSH/pytest (mandatory)**
- [docs/AGENTIC_ENGINEERING.md](../AGENTIC_ENGINEERING.md) — Spec -> GraphPre -> Plan -> Code -> GrepLoop -> Review -> Verify -> GraphPost
- [GREPLOOP.md](GREPLOOP.md) — mandatory `rg` loop for stale terms, old fields, missing evidence rows, and PR Greptile review when available

## Step 2 — Math invariants

- [BLUEPRINT.md](../../BLUEPRINT.md)
- [docs/REVIEWER_CHARTER.md](../REVIEWER_CHARTER.md) — Pass A + Pass B with PDF citations

## Step 3 — Operating the system (when needed)

Only after graph + charter: [docs/human/DOC_INDEX.md](../human/DOC_INDEX.md).

**CHI404 hardware/runtime (before infra edits):** [docs/ai/chi404_system_spec.json](chi404_system_spec.json) · human: [docs/chi404/HARDWARE_BASELINE.md](../chi404/HARDWARE_BASELINE.md)

## Step 4 — Verification

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
