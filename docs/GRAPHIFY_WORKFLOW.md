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
.\scripts\graphify_gate.ps1 -Query "where is X defined and what calls it"
.\scripts\graphify_pre_edit.ps1
```

On CHI404:

```bash
bash scripts/graphify_gate.sh "CHI404 R|Trader deploy paper latency"
bash scripts/graphify_pre_edit.ps1   # if using PowerShell on workstation only
```

**GraphGate is blocking:** `graphify_pre_edit.ps1` exits 2 if `graphify-out/.last-graph-query.json` is missing or older than 4 hours.

CHI404 / R|Trader tasks: read [docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md](vault/CHI404_CANONICAL_ENTRYPOINTS.md) after graph query.

## Post-edit (after Verify)

```powershell
.\scripts\graphify_rebuild.ps1
```

Equivalent manual command (mandatory after code edits, no API key):

```powershell
graphify update .
```

AST-only rebuild via `graphify update .` — **no LLM, no Google API** (mandatory after code edits).

Optional **full semantic rebuild** (PDFs/docs + inferred edges) via **local Ollama** — not Gemini:

```powershell
pip install openai   # graphify ollama backend uses OpenAI-compatible client
.\scripts\graphify_semantic_local.ps1
```

Manual equivalent:

```powershell
$env:OLLAMA_API_KEY = 'local'
graphify extract . --backend ollama --model "gemma4:31b-cloud" --max-concurrency 1 --api-timeout 600 --out .
```

Override model: `$env:GRAPHIFY_OLLAMA_MODEL = 'your-ollama-tag'`

Do **not** use `graphify .` / Gemini unless you explicitly want cloud API (`GEMINI_API_KEY`).

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
