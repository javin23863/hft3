# Vault notes

Durable research and ops baselines. Not runtime logs or secrets.

**ANY LLM agent session start (full roadmap):** Obsidian vault `architecture/Agent Runtime Roadmap` at  
`C:\Users\MSI\Desktop\Obsidian Vault From VPS\hft3\` — also mirrored in `wiki/hot.md` top section.

## Mandatory load order (repo + vault)

| # | Load | Repo path |
|---|------|-----------|
| 1 | **Fable mindset** | [FABLE_MINDSET.md](FABLE_MINDSET.md) · `.cursor/rules/00-fable-mindset.mdc` |
| 2 | **Ponytail mindset** | [docs/ai/PONYTAIL.md](../ai/PONYTAIL.md) · `.cursor/rules/01-ponytail-mindset.mdc` · `vendor/ponytail/` |
| 3 | Vault ontology | `scripts/vault_gate.ps1` · Obsidian `wiki/hot.md` |
| 4+ | Task-specific notes | Table below |

| Note | Purpose |
|------|---------|
| **[FABLE_MINDSET.md](FABLE_MINDSET.md)** | **#1** — Fable loop + what “latency test” means (µs offensive/defensive probe) |
| **Obsidian [[Agent Runtime Roadmap]]** | **ANY LLM onboarding** — Fable → Ponytail → gates → loop |
| **[AGENT_RUNTIME_ROADMAP.md](AGENT_RUNTIME_ROADMAP.md)** | Repo mirror of Obsidian agent roadmap |
| **[HFTBACKTEST_LATENCY_ONTOLOGY.md](HFTBACKTEST_LATENCY_ONTOLOGY.md)** | **Backtest/realism** — HftBacktest feed/entry/response, regimes, metric taxonomy |
| **[RITHMIC_LIVE_CONNECTION.md](RITHMIC_LIVE_CONNECTION.md)** | CHI404/Rithmic live login, endpoints, live safety |
| [CHI404_CANONICAL_ENTRYPOINTS.md](CHI404_CANONICAL_ENTRYPOINTS.md) | Native `rithmic_latency_probe` commands, forbidden paths |
| [RESEARCH_ENTRYPOINTS.md](RESEARCH_ENTRYPOINTS.md) | Canonical script order; legacy paths marked |
| [DATA_LAKE_3TIER.md](DATA_LAKE_3TIER.md) | **Data baseline (2026-06-12)** — 3-tier layout (C:\hft3-lake / CHI404 / B2 Hft3repo), env resolution, ledger, nightly automation, invariants |
| [WORKSTATION_ONE_LANE.md](WORKSTATION_ONE_LANE.md) | One Databento NPZ lane; keys verified; catalog vs runnable; ES fallback |
| [CPI_2024_09_11_TIGHT_BASELINE.md](CPI_2024_09_11_TIGHT_BASELINE.md) | CPI event replay + CHI404 latency baseline (2026-05-30) |
