# Graph Report - hft3  (2026-06-08)

## Corpus Check
- 42395 files · ~4,490,330 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 295 nodes · 502 edges · 21 communities (20 shown, 1 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 4 edges (avg confidence: 0.8)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `d46f49a4`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]

## God Nodes (most connected - your core abstractions)
1. `run_campaign()` - 20 edges
2. `str` - 15 edges
3. `str` - 12 edges
4. `Path` - 12 edges
5. `Path` - 10 edges
6. `Discovery` - 10 edges
7. `Confirmation` - 10 edges
8. `Holdout` - 10 edges
9. `Recent holdout` - 10 edges
10. `locations` - 10 edges

## Surprising Connections (you probably didn't know these)
- `main()` --references--> `int`  [EXTRACTED]
  runtime/audit_all_models_symbols_backtest_fast.py → apps/workbench/src/run/campaign_runner.py
- `start_campaign_subprocess()` --calls--> `make_campaign_id()`  [INFERRED]
  apps/workbench/src/run/job_manager.py → apps/workbench/src/run/campaign_runner.py
- `start_campaign_for_selection()` --calls--> `start_campaign_subprocess()`  [INFERRED]
  apps/workbench/ui/flow_state.py → apps/workbench/src/run/job_manager.py
- `start_campaign_for_selection()` --calls--> `set_control()`  [INFERRED]
  apps/workbench/ui/flow_state.py → apps/workbench/src/run/job_manager.py
- `poll_campaign_status()` --calls--> `get_job_status()`  [INFERRED]
  apps/workbench/ui/flow_state.py → apps/workbench/src/run/job_manager.py

## Communities (21 total, 1 thin omitted)

### Community 1 - "Community 1"
Cohesion: 0.09
Nodes (21): blocked_or_binding_errors, decadal_config, generated_at_utc, model_count, notes, overall, fill_count_total, fills_profitable (+13 more)

### Community 2 - "Community 2"
Cohesion: 0.15
Nodes (26): MES.v.0, RTY.v.0, MES.v.0, RTY.v.0, MES.v.0, RTY.v.0, MES.v.0, error (+18 more)

### Community 3 - "Community 3"
Cohesion: 0.32
Nodes (7): _load_sessions(), main(), _npz_present(), bool, int, str, Return the list of runnable (non-skip_pull) decadal sessions.

### Community 4 - "Community 4"
Cohesion: 0.16
Nodes (18): count_any(), count_npz_near(), count_raw_dbn(), main(), (file_count, total_bytes), derive_npz(), main(), _npz_path() (+10 more)

### Community 5 - "Community 5"
Cohesion: 0.14
Nodes (13): generated_at_utc, model_count, model_symbol_combos, models_meta, overall, event_slots_npz_ready, event_slots_total, full_matrix_ready_today (+5 more)

### Community 6 - "Community 6"
Cohesion: 0.29
Nodes (12): ES.v.0, ES.v.0, ES.v.0, error, npz_ready, pct_ready, periods, total_events (+4 more)

### Community 7 - "Community 7"
Cohesion: 0.29
Nodes (12): MNQ.v.0, MNQ.v.0, MNQ.v.0, npz_ready, pct_ready, total_events, error, npz_ready (+4 more)

### Community 8 - "Community 8"
Cohesion: 0.54
Nodes (8): NQ.v.0, NQ.v.0, NQ.v.0, error, npz_ready, pct_ready, periods, total_events

### Community 9 - "Community 9"
Cohesion: 0.22
Nodes (22): Any, bool, ModelComposition, Path, str, campaign_base(), campaign_progress_panel(), event_artifact_dir() (+14 more)

### Community 10 - "Community 10"
Cohesion: 0.29
Nodes (12): ZB.v.0, ZB.v.0, ZB.v.0, npz_ready, pct_ready, total_events, Discovery, error (+4 more)

### Community 11 - "Community 11"
Cohesion: 0.54
Nodes (8): ZN.v.0, ZN.v.0, ZN.v.0, error, npz_ready, pct_ready, periods, total_events

### Community 12 - "Community 12"
Cohesion: 0.05
Nodes (38): info, npz_bytes, npz_files, npz_gb, path, download_mbo, path, raw_dbn_files (+30 more)

### Community 13 - "Community 13"
Cohesion: 0.08
Nodes (24): cme_readiness, event_slots_npz_ready, event_slots_total, full_matrix_ready_today, pct_ready, generated_at_utc, model_pct_distribution, 0% (+16 more)

### Community 14 - "Community 14"
Cohesion: 0.29
Nodes (6): derived, failed, generated_at_utc, pending, skipped_existing, skipped_no_dbn

### Community 15 - "Community 15"
Cohesion: 0.50
Nodes (3): has_npz(), bool, str

### Community 16 - "Community 16"
Cohesion: 0.18
Nodes (29): Any, bool, ModelComposition, Path, str, int, _campaign_id(), CampaignResult (+21 more)

### Community 19 - "Community 19"
Cohesion: 0.29
Nodes (12): Any, bool, ModelComposition, Path, str, Popen, get_job_status(), job_dir_for() (+4 more)

### Community 20 - "Community 20"
Cohesion: 0.33
Nodes (5): Path, _artifact_base(), _extract_aggregate_from_periods(), Streamlit microstructure workbench UI., Extract net_pnl + num_trades from summary-level periods list.

## Knowledge Gaps
- **117 isolated node(s):** `ModelComposition`, `bool`, `ModelComposition`, `Popen`, `Any` (+112 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `locations` connect `Community 12` to `Community 13`?**
  _High betweenness centrality (0.035) - this node is a cross-community bridge._
- **Why does `start_campaign_subprocess()` connect `Community 19` to `Community 16`, `Community 9`?**
  _High betweenness centrality (0.029) - this node is a cross-community bridge._
- **Why does `per_model_symbol` connect `Community 2` to `Community 5`?**
  _High betweenness centrality (0.027) - this node is a cross-community bridge._
- **What connects `ModelComposition`, `Walk-forward campaign orchestrator (B4 sequential gates).`, `Read-only market-state residency snapshot (diagnostics only).` to the rest of the system?**
  _133 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.09090909090909091 - nodes in this community are weakly interconnected._
- **Should `Community 5` be split into smaller, more focused modules?**
  _Cohesion score 0.14285714285714285 - nodes in this community are weakly interconnected._
- **Should `Community 12` be split into smaller, more focused modules?**
  _Cohesion score 0.05263157894736842 - nodes in this community are weakly interconnected._