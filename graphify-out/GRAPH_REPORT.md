# Graph Report - hft3  (2026-06-07)

## Corpus Check
- 42421 files · ~4,752,685 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 148 nodes · 240 edges · 17 communities
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `b6277d23`
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

## God Nodes (most connected - your core abstractions)
1. `Discovery` - 10 edges
2. `Confirmation` - 10 edges
3. `Holdout` - 10 edges
4. `Recent holdout` - 10 edges
5. `ABSORPTION_FADE` - 8 edges
6. `periods` - 8 edges
7. `AGGRESSOR_DECELERATION_FADE` - 8 edges
8. `BOOK_PRESSURE` - 8 edges
9. `periods` - 7 edges
10. `periods` - 7 edges

## Surprising Connections (you probably didn't know these)
- None detected - all connections are within the same source files.

## Communities (17 total, 0 thin omitted)

### Community 1 - "Community 1"
Cohesion: 0.10
Nodes (20): events_csv, events_csv_rows, generated_at_utc, method, model_count, model_symbol_combos, models_meta, overall (+12 more)

### Community 2 - "Community 2"
Cohesion: 0.30
Nodes (14): MES.v.0, MES.v.0, MES.v.0, MES.v.0, error, npz_ready, pct_ready, periods (+6 more)

### Community 3 - "Community 3"
Cohesion: 0.54
Nodes (8): ES.v.0, ES.v.0, ES.v.0, error, npz_ready, pct_ready, periods, total_events

### Community 4 - "Community 4"
Cohesion: 0.54
Nodes (8): MNQ.v.0, MNQ.v.0, MNQ.v.0, error, npz_ready, pct_ready, periods, total_events

### Community 5 - "Community 5"
Cohesion: 0.54
Nodes (8): NQ.v.0, NQ.v.0, NQ.v.0, error, npz_ready, pct_ready, periods, total_events

### Community 6 - "Community 6"
Cohesion: 0.54
Nodes (8): RTY.v.0, RTY.v.0, RTY.v.0, error, npz_ready, pct_ready, periods, total_events

### Community 7 - "Community 7"
Cohesion: 0.54
Nodes (8): ZB.v.0, ZB.v.0, ZB.v.0, error, npz_ready, pct_ready, periods, total_events

### Community 8 - "Community 8"
Cohesion: 0.54
Nodes (8): ZN.v.0, ZN.v.0, ZN.v.0, error, npz_ready, pct_ready, periods, total_events

### Community 9 - "Community 9"
Cohesion: 0.50
Nodes (4): npz_ready, pct_ready, total_events, Confirmation

### Community 10 - "Community 10"
Cohesion: 0.50
Nodes (4): npz_ready, pct_ready, total_events, Discovery

### Community 11 - "Community 11"
Cohesion: 0.50
Nodes (4): npz_ready, pct_ready, total_events, Holdout

### Community 12 - "Community 12"
Cohesion: 0.50
Nodes (4): Recent holdout, npz_ready, pct_ready, total_events

### Community 13 - "Community 13"
Cohesion: 0.11
Nodes (18): backtester_version, blocking_failures, covered_event_types, covered_execution_modes, covered_latency_bands, covered_modules, covered_queue_models, covered_symbols (+10 more)

### Community 14 - "Community 14"
Cohesion: 0.13
Nodes (14): backtester_version, blocking_failures, covered_event_types, covered_execution_modes, covered_latency_bands, covered_modules, covered_queue_models, covered_symbols (+6 more)

### Community 15 - "Community 15"
Cohesion: 0.20
Nodes (9): command, duration_sec, failed_count, git_sha, passed, pytest_output_tail, test_count, tier (+1 more)

### Community 16 - "Community 16"
Cohesion: 0.50
Nodes (3): Backtester Certification Scorecard, T0 Fast Gate, T2 Full Suite

## Knowledge Gaps
- **74 isolated node(s):** `tier`, `status`, `run_id`, `git_sha`, `timestamp_utc` (+69 more)
  These have ≤1 connection - possible missing edges or undocumented components.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `per_model_symbol` connect `Community 2` to `Community 1`?**
  _High betweenness centrality (0.157) - this node is a cross-community bridge._
- **Why does `ABSORPTION_FADE` connect `Community 2` to `Community 3`, `Community 4`, `Community 5`, `Community 6`, `Community 7`, `Community 8`?**
  _High betweenness centrality (0.083) - this node is a cross-community bridge._
- **Why does `AGGRESSOR_DECELERATION_FADE` connect `Community 2` to `Community 3`, `Community 4`, `Community 5`, `Community 6`, `Community 7`, `Community 8`?**
  _High betweenness centrality (0.083) - this node is a cross-community bridge._
- **What connects `tier`, `status`, `run_id` to the rest of the system?**
  _74 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.09523809523809523 - nodes in this community are weakly interconnected._
- **Should `Community 13` be split into smaller, more focused modules?**
  _Cohesion score 0.10526315789473684 - nodes in this community are weakly interconnected._
- **Should `Community 14` be split into smaller, more focused modules?**
  _Cohesion score 0.13333333333333333 - nodes in this community are weakly interconnected._