# Contributing to hft3

## Trunk-based workflow

- All work merges to **`main`** via pull request.
- No long-lived feature branches; no parallel `develop` branch.
- Keep docs in one chronological path: [docs/human/DOC_INDEX.md](docs/human/DOC_INDEX.md).

## Before you start

| Role | Start here |
|------|------------|
| Human developer | [docs/human/GETTING_STARTED.md](docs/human/GETTING_STARTED.md) |
| AI / Cursor agent | [docs/ai/ONBOARDING.md](docs/ai/ONBOARDING.md) → graph first |
| Coding style | [docs/ai/ENGINEERING.md](docs/ai/ENGINEERING.md) + [AGENTS.md](AGENTS.md) |

## Required checks

1. **Agent verify (bounded):** `powershell -File scripts/run_agent_verify.ps1` (180s cap — T0 + registry + workbench)
2. **T0 backtester gate:** `python -m pytest tests/backtester_validation/fast -q` (90s cap)
3. **Full unit suite:** only when needed; use [docs/ai/SHELL_EXECUTION.md](docs/ai/SHELL_EXECUTION.md) budgets (600s cap)
4. **Graph rebuild** after code edits: `graphify update .` or `scripts/graphify_rebuild.ps1` (300s cap)
5. **Dual-pass review** per [docs/REVIEWER_CHARTER.md](docs/REVIEWER_CHARTER.md) for non-trivial changes

Long commands: `tools/shell/run_with_timeout.ps1` / `run_with_timeout.sh`. Agents: [.cursor/rules/shell-execution-timeouts.mdc](.cursor/rules/shell-execution-timeouts.mdc).

## Code navigation

```bash
graphify query "your question"
graphify explain ReplaySession
```

Do not rely on blind repo-wide grep when the graph can scope the search.

## Topology

Live/paper market data and orders: **CHI404 only**. See [BLUEPRINT.md](BLUEPRINT.md) §4.

## Secrets

Never commit `.env`, credentials, or API keys. Use `.env.example` as template.

## Artifact layout

- Research outputs: `artifacts/` (formerly `research_cards/`)
- Machine ephemeral: `runtime/`
- Contract: [docs/human/RUNTIME_CONTRACT.md](docs/human/RUNTIME_CONTRACT.md)
