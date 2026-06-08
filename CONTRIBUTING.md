# Contributing

## Branch naming

| Prefix | Use |
|--------|-----|
| `feature/` | New functionality |
| `bugfix/` | Bug or regression fix |
| `chore/` | Repo maintenance, cleanup, refactoring |
| `experiment/` | Research spike, throwaway exploration |
| `codex/` | LLM-generated bulk work (.md, scaffold, config audit) |

## Commit messages

- Imperative mood, capitalised, no period: `Add MBOEvent adapter to stocks lane`
- One line preferred; body only for non-obvious rationale
- Reference issue/PR number when applicable

## Before pushing

1. `python -m pytest tests/ -q` — all tests pass
2. `python hft3_bootstrap.py` — bootstrap resolves
3. Graphify rebuild (if code changed): `graphify update .` or `scripts/graphify_rebuild.ps1`

## Review

Every diff undergoes **two-pass review** per [docs/REVIEWER_CHARTER.md](docs/REVIEWER_CHARTER.md):
- **Pass A** (Karpathy): simplicity, surgical scope, verifiable criteria
- **Pass B** (domain): microstructure math, PDF-model invariants, certification tiers

Agentic workflow: see [AGENTS.md](AGENTS.md) and [docs/AGENTIC_ENGINEERING.md](docs/AGENTIC_ENGINEERING.md).
