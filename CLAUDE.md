# CLAUDE.md — hft3 navigation and memory wiring

## Fable mindset (standing operating discipline)

Before touching hft3, use the Fable loop: ground in real state, reason before action, act in deliberate batches, observe, re-evaluate, read exact regions before edits, verify with real checks, recover by diagnosis, and report truthfully. Use `C:\Users\MSI\.codex\skills\fable-mindset\references\Fable_Mindset_public.md` as the full reference when the task is long-running, high-risk, finance/math-critical, or the user asks to reestablish mindset.

## Ponytail mindset (mandatory after Fable)

Apply the ponytail lazy-senior-dev ladder on every implementation: YAGNI → stdlib → native platform → installed dep → one line → minimum that works. Repo: https://github.com/DietrichGebert/ponytail · vendored at `vendor/ponytail/` · always-on Cursor rule `.cursor/rules/ponytail.mdc` · charter [docs/ai/PONYTAIL.md](docs/ai/PONYTAIL.md). Use `/ponytail-review` on diffs when the host supports skills. **Not lazy about:** trust-boundary validation, data-loss errors, security, accessibility, BLUEPRINT math invariants. Vast rent/SSH/sync: `vastai` CLI + `scripts/vast_ssh_run_vbt_paid_screen.sh` (set `VAST_SSH_HOST`, `VAST_SSH_PORT`).

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
- Live/paper paths are lane-scoped: CME on CHI404 only, crypto on the Contabo BTC-node VPS only (specs/CRYPTO_LIVE.md §2); this workstation is offline research only for every lane.
- Rithmic trial data is quarantined; never write to `data/npz/`.
- T0 gate before merge: `python -m pytest tests/backtester_validation/fast -q`.
- Use `workbench.src.artifacts.paths` helpers; never hardcode artifact paths.
