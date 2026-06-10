# CLAUDE.md — hft3 navigation and memory wiring

## Knowledge graph first (structural memory)

Before any repo-wide grep or file exploration, query the code graph:

```bash
graphify query "where is ReplaySession defined?"
graphify explain <Symbol>
graphify path <caller> <callee>
```

Entry point: `graphify-out/wiki/index.md` (freshness header shows build date + commit — rebuild with `scripts/graphify_rebuild.ps1` if stale). This complements the mandatory GraphGate workflow in `AGENTS.md`; never skip GraphGate, GraphPre, or GraphPost.

## Obsidian vault (declarative memory)

Vault: `C:\Users\MSI\Desktop\Obsidian Vault From VPS\hft3\`

- `Home.md` — index of the curated knowledge base (architecture, pipelines, certification, ops)
- `Memory Stack.md` — the protocol for using graph + vault together
- `decisions/` — prior decisions; read relevant ones before designing, append one after non-trivial changes
- `sessions/` — session logs
- `library/` — math literature ontology (microstructure/LOB/Hawkes/execution papers) with `System Implications.md` mapping literature → required code/tests

Consult the vault before re-deriving architecture or re-litigating decisions; write back what future sessions need.

## Hard rules (from AGENTS.md / BLUEPRINT.md — see those for full charter)

- Filtration F_t: no lookahead in features/signals.
- Live/paper paths run on CHI404 only; this workstation is offline research only.
- Rithmic trial data is quarantined; never write to `data/npz/`.
- T0 gate before merge: `python -m pytest tests/backtester_validation/fast -q`.
- Use `workbench.src.artifacts.paths` helpers; never hardcode artifact paths.
