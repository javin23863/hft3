# Graphify workflow (hft3)

Mandatory for orchestrators: **consult the graph before edits**, **rebuild after edits**. Cursor rule: [.cursor/rules/graphify-mandatory.mdc](../.cursor/rules/graphify-mandatory.mdc).

## Install

```powershell
pip install graphifyy
graphify cursor install --project
```

Verify:

```powershell
graphify --version
```

## Pre-edit (before Plan / Code)

From repo root (`C:\...\hft3`):

```powershell
.\scripts\graphify_pre_edit.ps1
graphify query "where is X defined and what calls it"
```

If `graphify-out/graph.json` is missing, the pre-edit script runs `graphify update .` (AST-only, no API key).

Alternative when `graphify-out/GRAPH_REPORT.md` is fresh: read that report instead of repeating broad grep.

**Prefer** `graphify query` over blind full-repo grep for symbol location and call relationships.

## Post-edit (after Verify)

```powershell
.\scripts\graphify_rebuild.ps1
```

Equivalent manual command (mandatory after code edits, no API key):

```powershell
graphify update .
```

AST-only rebuild via `graphify update .` — no LLM key required.

Optional full semantic rebuild (includes PDFs; requires `GEMINI_API_KEY` or `ANTHROPIC_API_KEY`):

```powershell
graphify .
```

On failure the rebuild script falls back to `graphify cluster-only .`. Logs: `logs/graphify/rebuild.log`.

## Windows PowerShell notes

- Run scripts with `.\scripts\...` — **no leading slash** (`/scripts/...` is wrong on Windows).
- Use repo root as current directory before `graphify` or helper scripts.
- `graphify` must be on `PATH` (activate the same venv you used for `pip install graphifyy`).

## Team git workflow for `graphify-out/`

- **Commit** graph artifacts the team relies on (`graph.json`, `GRAPH_REPORT.md`, indexes, etc.).
- **Do not commit** local-only metadata: `graphify-out/manifest.json` and `graphify-out/cost.json` are gitignored (see `.gitignore`).
- After your branch changes Python/modules, rebuild graph before push so `graphify-out/` matches code.
- If CI or reviewers see stale graph output, run `.\scripts\graphify_rebuild.ps1` and include the diff.

## Related

- [AGENTS.md](../AGENTS.md) — Spec → GraphPre → Plan → Code → Verify → GraphPost
- [AGENTIC_ENGINEERING.md](AGENTIC_ENGINEERING.md) — workflow diagram
