# hft3 Code Graph — AI Entry Point

> **Freshness:** Built `2026-06-05T14:44:50.276756+00:00` | Graph commit `4d491d55077745a844b96a10bbc2c521f0908d7c` | AST nodes `27178`

Read this file **before** prose docs. Then use `graphify query`, `graphify path`, or `graphify explain`.

## Start here

```bash
graphify query "where is ReplaySession defined?"
graphify explain ReplaySession
graphify path run_event_replay ReplaySession
```

## Key concepts (graphify explain)

- `ReplaySession` — run: `graphify explain "ReplaySession"`
- `build_certification_stamp` — run: `graphify explain "build_certification_stamp"`
- `run_after_action_report` — run: `graphify explain "run_after_action_report"`
- `CampaignRunner` — run: `graphify explain "CampaignRunner"`
- `run_event_replay` — run: `graphify explain "run_event_replay"`
- `MBOFeatureExtractor` — run: `graphify explain "MBOFeatureExtractor"`
- `HypothesisRegistry` — run: `graphify explain "HypothesisRegistry"`

## Top communities

- Community 0: Community 0 — Core infrastructure
- Community 1: Community 1 — Backtest pipeline
- Community 2: Community 2 — Feature engine
- Community 3: Community 3 — Workbench
- Community 4: Community 4 — Execution / replay
- Community 5: Community 5 — Data ingest
- Community 6: Community 6
- Community 7: Community 7
- Community 8: Community 8
- Community 9: Community 9
- Community 10: Community 10
- Community 11: Community 11

## Human docs (after graph)

- [docs/ai/ONBOARDING.md](../../docs/ai/ONBOARDING.md)
- [AGENTS.md](../../AGENTS.md)
- [docs/human/DOC_INDEX.md](../../docs/human/DOC_INDEX.md)

## Rebuild graph

```bash
graphify update .
# or: scripts/graphify_rebuild.ps1
python tools/graphify/build_wiki_index.py
```

