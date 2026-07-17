# Graph Report - hft3-pr10-gate  (2026-06-22)

## Corpus Check
- 5594 files · ~2,936,271 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 836 nodes · 1364 edges · 52 communities (49 shown, 3 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS · INFERRED: 2 edges (avg confidence: 0.8)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `41ce228b`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
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
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]

## God Nodes (most connected - your core abstractions)
1. `_read_universe_stage()` - 49 edges
2. `_load_v2_module()` - 40 edges
3. `_write_screening_artifact()` - 34 edges
4. `_json_roundtrip()` - 24 edges
5. `Path` - 18 edges
6. `_robustness_with_dsr_cell()` - 18 edges
7. `Queue` - 18 edges
8. `Path` - 18 edges
9. `_options_ok_checks()` - 16 edges
10. `_stub_q001_ok()` - 16 edges

## Surprising Connections (you probably didn't know these)
- `_format_report()` --references--> `Any`  [EXTRACTED]
  scripts/run_autoresearch_three_gen_acceptance.py → packages/backtest_pipeline/src/chi404_latency.py
- `_parent_child_recipe_changes()` --references--> `Any`  [EXTRACTED]
  scripts/run_autoresearch_three_gen_acceptance.py → packages/backtest_pipeline/src/chi404_latency.py
- `_passing_surface_metrics()` --references--> `Any`  [EXTRACTED]
  scripts/run_autoresearch_three_gen_acceptance.py → packages/backtest_pipeline/src/chi404_latency.py
- `run_three_gen_acceptance()` --references--> `Any`  [EXTRACTED]
  scripts/run_autoresearch_three_gen_acceptance.py → packages/backtest_pipeline/src/chi404_latency.py
- `_seed_candidates_for_generation()` --references--> `Any`  [EXTRACTED]
  scripts/run_autoresearch_three_gen_acceptance.py → packages/backtest_pipeline/src/chi404_latency.py

## Communities (52 total, 3 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.14
Nodes (10): BaseContext, Process, Queue, _load_v2_module(), _spawn_test_echo_worker(), TestDrainWorkersEarlyWorkerExit, TestDrainWorkersPartialWorkerFailure, TestDrainWorkersWallClockBudget (+2 more)

### Community 1 - "Community 1"
Cohesion: 0.11
Nodes (14): _die_budget_then_echo_worker(), _echo_fast_fail_worker(), _immediate_worker_exit(), Focused tests for paid-screen v2 orchestrator and vast launcher dispatch., Spawn target: exit without result for the first N batches, then fast-fail., Spawn target: stay alive without producing batch results., Spawn target: simulate init/import failure before any batch result., Spawn target: immediately return ERROR results for each dispatched batch. (+6 more)

### Community 2 - "Community 2"
Cohesion: 0.05
Nodes (41): Authority links, code:bash (python scripts/vbt_paid_screen_next_steps.py), code:bash (cd "$HFT3_REPO"), code:bash (bash scripts/run_vbt_paid_screen_vast_full.sh), code:powershell ($env:VAST_SSH_HOST = 'root@<vast-host>'), code:bash (tmux new -s vbt_full), code:bash (python scripts/validate_paid_screen_ready_gate.py \), code:bash (jq '{completed,failed,skipped,expected: .expected_work_units) (+33 more)

### Community 3 - "Community 3"
Cohesion: 0.06
Nodes (32): B1 Generate smoke units, B2 Run smoke orchestrator, code:mermaid (flowchart TD), code:bash (git clone / sync hft3), code:bash (bash scripts/run_vbt_paid_screen_vast_full.sh), code:bash (export VBT_FULL_RUN_ID="paid_full_$(date -u +%Y%m%dT%H%M%SZ)), code:bash (python scripts/validate_paid_screen_ready_gate.py --watch-ma), code:bash (bash scripts/run_vbt_hbt_handoff_verify.sh) (+24 more)

### Community 4 - "Community 4"
Cohesion: 0.10
Nodes (23): Path, Tests for VectorBT paid screen gate and unit generation., Full-scope generator expands active registry (not single model or Stage A)., Full scope uses all TIGHT events per cell — not [:50] and not CPI+NFP-only smoke, Every Stage-A-style thesis must parse back to the same canonical slug., test_aggregate_promoted_ids(), test_all_active_default_excludes_holdout_events(), test_all_active_holdout_split_includes_holdout_when_explicit() (+15 more)

### Community 5 - "Community 5"
Cohesion: 0.23
Nodes (7): make_context(), make_unit(), A batch-level failure (no data) is recorded in the profiler., One unit's failure does not block other units in the batch.          With no d, Two BatchingKeys differing in any semantic field produce different         cach, TestBatchingKeyMismatch, WorkerContext

### Community 6 - "Community 6"
Cohesion: 0.19
Nodes (7): Resumability: the orchestrator must skip valid artifacts and recompute     inva, A JSON object missing required fields must fail validation., When resuming, units with existing valid artifacts should be skipped., When resuming, units with invalid artifacts should be recomputed., A mixed set: valid artifacts are skipped, invalid ones recomputed., TestInterruptAndResume, TestResumability

### Community 7 - "Community 7"
Cohesion: 0.11
Nodes (17): Autoresearch Performance Audit — Phase 5 (2026-06-20), Benchmark methodology (identical-scope projection), code:json ({), code:powershell ($env:PYTHONPATH=".;packages;apps"), code:block3 (merge-ready: no), Counters (evidence fields), Entrypoints, Executive summary (+9 more)

### Community 8 - "Community 8"
Cohesion: 0.22
Nodes (5): Path, Mirror run_vbt_paid_screen_vast_full.sh v2 hash resolution (fail-closed)., _resolve_vast_launch_hashes(), TestVastLauncherHashWiring, TestWorkerScratchPath

### Community 9 - "Community 9"
Cohesion: 0.17
Nodes (9): code:block1 (.cursor/plans/autonomous_research_pipeline_gate_chain.plan.m), Execution notes for owner, File-count sanity check (from `gh pr view 7 --json files`), Greptile PR split plan — PR #7 (`cursor/vast-vbt-workflow`), merge-ready status (this follow-up), PR-A — Autoresearch gate chain core (Phases 0–4), PR-B — Paid-screen v2 perf + Vast deploy contract, PR-C — Phases 5–7 + promotion gate math fix (current tail) (+1 more)

### Community 10 - "Community 10"
Cohesion: 0.18
Nodes (4): Critical: manifest must NOT say 'complete' when failed > 0., Exit code 0 only when status is complete; 1 otherwise.          Mirrors the or, The v2 orchestrator returns ``1 if failed else 0``; this must agree         wit, TestManifestStatusCorrectness

### Community 11 - "Community 11"
Cohesion: 0.03
Nodes (3): Cockpit backend tests — aggregator shape, graceful-missing, API auth.  Run fro, BLUEPRINT §8: explicit holdout research_split must not yield VectorBT Screen OK., test_vectorbt_holdout_research_split_tracking_stage_stale()

### Community 12 - "Community 12"
Cohesion: 0.22
Nodes (8): cavecrew receipts, code:text (.\.venv\Scripts\python.exe -m pytest tests/research_pipeline), code:text (merge-ready: no (Greptile PENDING on 8c5c1ec0)), Gate-order compliance, Greptile (`8c5c1ec0`), Greptile zero-tolerance reset — 2026-06-20 (updated), Validation honesty, verify-run

### Community 13 - "Community 13"
Cohesion: 0.22
Nodes (5): A corrupted screening_artifact.json must not be treated as valid., A partial JSON file (simulating a crash mid-write) must not validate., An artifact missing required fields must fail validation., An empty artifact file (zero bytes) must not validate., TestCorruptedArtifactRecovery

### Community 14 - "Community 14"
Cohesion: 0.22
Nodes (5): A low memory limit should evict old entries, not crash., Worker._recycle() clears the cache and resets the batch counter., Worker.should_recycle() returns True at the limit., process_batch() automatically recycles when the limit is reached., TestMemoryLimitRecycling

### Community 15 - "Community 15"
Cohesion: 0.22
Nodes (5): Every failure diagnostic must contain the required fields., Failure diagnostics are written to a JSON file., An empty failure list still produces a valid (empty) JSON file., The full traceback must be captured, not empty., TestFailureDiagnostics

### Community 16 - "Community 16"
Cohesion: 0.22
Nodes (8): 1. Fable loop (always), 2. Ponytail mindset (mandatory second — before codebase), 3. Run shape (latency test on CHI404), 3. When the operator says **“latency test”**, 4. Repo vault read order (after this note), 5. Hard rejects (latency), code:bash (cd /root/hft3/repo), Fable mindset — load this first (every agent session)

### Community 17 - "Community 17"
Cohesion: 0.25
Nodes (4): A corrupted cache entry (None value) must not crash the system., After clearing a corrupted cache, new entries work correctly., Invalidating a corrupted entry works and frees space., TestCorruptedCacheRecovery

### Community 18 - "Community 18"
Cohesion: 0.29
Nodes (6): code:text (merge-ready: no), Greptile poll (`8c5c1ec0`, 12 min), Greptile PR #10 loop — 2026-06-20 (Phase 9 PR-C), Iteration table, merge-ready (PR-C), Validation honesty

### Community 19 - "Community 19"
Cohesion: 0.33
Nodes (5): Intensity (optional), Minimal usage (from ponytail docs), Ponytail — mandatory agent coding discipline, Vast operations (hft3 — not ponytail), When agents MUST apply ponytail

### Community 20 - "Community 20"
Cohesion: 0.47
Nodes (5): main(), int, str, Drop legacy v1/v2 selector; warn if v1 was requested., _strip_retired_flags()

### Community 23 - "Community 23"
Cohesion: 1.00
Nodes (3): vast_remote_verify.sh script, fail(), need_env()

### Community 24 - "Community 24"
Cohesion: 0.50
Nodes (3): run_vbt_paid_screen_vast_full.sh script, HFT3_MANIFEST_PATH, VBT_FULL_RUN_ID

### Community 25 - "Community 25"
Cohesion: 0.06
Nodes (62): bool, int, Path, str, _allow_screening_validation(), _hftbacktest_source_lock(), _point_latency_paths(), _point_vbt_tracking_paths() (+54 more)

### Community 26 - "Community 26"
Cohesion: 0.06
Nodes (44): _read_universe_stage(), _robustness_with_dsr_cell(), test_universe_aborted_artifact_is_fail(), test_universe_all_empty_or_skip_only_artifact_is_stale(), test_universe_bare_dsr_pass_without_cdf_or_signed_alias_fails_closed(), test_universe_bounded_smoke_with_evaluated_model_is_stale(), test_universe_declared_skip_reason_counts_prevent_runtime_double_count(), test_universe_dsr_cdf_above_probability_range_fails_closed() (+36 more)

### Community 27 - "Community 27"
Cohesion: 0.13
Nodes (30): bool, int, str, _active_job(), _all_jobs(), _audit(), _audit_tail(), autonomy_stop() (+22 more)

### Community 28 - "Community 28"
Cohesion: 0.09
Nodes (27): _json_roundtrip(), _point_options_zone_ok(), A synthetic data_doctor report with options-* checks (incl. one FAIL) and an, test_lanes_synthetic_data_doctor_report(), test_options_context_coverage_absent_summary_preserves_not_measured(), test_options_context_coverage_claim_missing_proof_fails_closed(), test_options_context_coverage_explicit_not_measured_contract(), test_options_context_coverage_future_source_timestamp_fails_closed() (+19 more)

### Community 29 - "Community 29"
Cohesion: 0.11
Nodes (42): Any, CandidateModel, float, ParsedHypothesis, Path, _acceptance_filter(), _acceptance_hft_fn(), _acceptance_persist() (+34 more)

### Community 30 - "Community 30"
Cohesion: 0.16
Nodes (23): int, str, alerts(), all_zones(), autonomy(), chat(), _client_ip_for_rate_limit(), lifecycle() (+15 more)

### Community 31 - "Community 31"
Cohesion: 0.08
Nodes (23): §22 — positive pearson/spearman (workbench + bridge; not autoresearch receipts), §2 — autoresearch entrypoints, §2 — gate chain symbols (expected absent pre-Phase 1), §2 — Greptile (docs only; not in `.github`), §2 — Martyn Tinsley / WFC, §2 — permissive autoresearch / elite, Audit table, Authority / transcript anchors (+15 more)

### Community 32 - "Community 32"
Cohesion: 0.17
Nodes (15): _full_universe_cli_args(), _point_non_universe_pipeline_paths(), test_pipeline_active_run_guard_allows_matching_universe_artifact(), test_pipeline_active_run_guard_rejects_mismatched_generated_artifacts(), test_pipeline_build_includes_universe_sweep_tracking(), test_pipeline_falls_back_to_smoke_when_full_m6_absent(), test_pipeline_missing_universe_does_not_hide_missing_prerequisites(), test_pipeline_prefers_full_m6_artifact_when_present() (+7 more)

### Community 33 - "Community 33"
Cohesion: 0.11
Nodes (18): Active feature line (not on `main` yet), `C:\Users\MSI\Documents\New project`, `C:\Users\MSI\repos\hft3`, Canonical working copy, Chronological trunk (GitHub `main`), Cleanup pass (2026-06-19, this workspace), code:block1 (origin/codex/vbt-hbt-handoff), code:block2 (origin/chore/repo-cleanup-and-data-fill) (+10 more)

### Community 34 - "Community 34"
Cohesion: 0.21
Nodes (16): _options_ok_checks(), _silence_alert_sources(), test_alerts_q001_fail_is_crit_and_red(), test_alerts_q001_missing_artifact_warns(), test_alerts_q001_ok_emits_no_q001_alert(), test_alerts_q001_runtime_warning_rolls_up_without_raw_strict_mbo_alert(), test_alerts_quiet_when_healthy(), test_lanes_strict_mbo_warn_is_advisory() (+8 more)

### Community 35 - "Community 35"
Cohesion: 0.14
Nodes (13): code:text (.\.venv\Scripts\python.exe -m pytest \), code:text (merge-ready: no (PR-B)), Greptile iteration log, Greptile PR #9 loop report — 2026-06-20, Inline classification on `b300183b`, merge-ready (PR-B), Next step, Owner waive + merge (option B) — 2026-06-20T08:57Z (+5 more)

### Community 36 - "Community 36"
Cohesion: 0.15
Nodes (15): Pointing DATA_DOCTOR_REPORT at a nonexistent file -> cme_options_data.status==mi, alerts zone with a failing options-fixing-coverage check -> alert id     'lake-, _stub_q001_ok(), _stub_research_replay_system_ok(), test_alerts_missing_data_doctor_report(), test_alerts_missing_mandatory_options_checks(), test_alerts_options_defect_ledger_open_is_not_runtime_alert(), test_alerts_options_fixing_coverage_alert() (+7 more)

### Community 37 - "Community 37"
Cohesion: 0.27
Nodes (5): PaidScreenUnit, _copy_valid_unit_artifact(), _matching_paid_batch_unit(), TestResumeArtifactContextValidation, TestV2ManifestRelpaths

### Community 38 - "Community 38"
Cohesion: 0.20
Nodes (7): _invoke_main(), int, str, main() must terminate promptly after drain+shutdown (mocked 128 workers)., Call orchestrator main(); normalize ``return`` and ``sys.exit`` to int., TestOrchestratorMainExit, TestV2RunHashResolution

### Community 39 - "Community 39"
Cohesion: 0.28
Nodes (3): fmtMs(), fmtUs(), LatencyBand()

### Community 40 - "Community 40"
Cohesion: 0.25
Nodes (7): Autoresearch three-generation acceptance (2026-06-20), Blockers / honesty, Generation 0, Generation 1, Generation 1 parent-child recipe changes, Generation 2, Generation 2 parent-child recipe changes

### Community 41 - "Community 41"
Cohesion: 0.25
Nodes (7): Classification (current head), code:block1 (pytest tests/research_pipeline/ -q), Greptile activity (poll 2026-06-20), merge-ready (PR-A), Phase 9 step (this poll), pr-greptile-review (PR-A), verify-run

### Community 42 - "Community 42"
Cohesion: 0.20
Nodes (7): _batch_cache_key(), A crashing worker / failing batch must not block other units., A cache hit for the shared data must not mask a per-unit failure., ``worker_process_main`` records a failure and emits an empty result         lis, Spawn entrypoint must set lake env vars before worker init., Reproduce the symbol-aware cache key used by screen_paid_batch., TestWorkerCrashRecovery

### Community 43 - "Community 43"
Cohesion: 0.36
Nodes (3): _declaration_run_id(), Mirror run_vbt_paid_screen_vast_full.sh declaration run-id extraction., TestVastLauncherRunIdDerivation

### Community 44 - "Community 44"
Cohesion: 0.29
Nodes (6): Autoresearch final checklist — PR-A scope (Phase 10 prep), code:block1 (.venv\Scripts\python.exe -m pytest tests/research_pipeline/t), code:block2 (merge-ready:     no), PR-A requirement rows (§25 subset), Validation honesty, Verify (PR-A scope)

### Community 45 - "Community 45"
Cohesion: 0.29
Nodes (6): Code fixes (cumulative), Confidence scores observed, Greptile PR #8 loop — 2026-06-20, Iteration log, merge-ready (PR-A), PR #9 correction

### Community 46 - "Community 46"
Cohesion: 0.29
Nodes (6): bool, _artifact_relpath(), Path, str, Faithful copy of the orchestrator's resume predicate., Mirror the v2 orchestrator's per-unit artifact layout.

### Community 47 - "Community 47"
Cohesion: 0.60
Nodes (5): _accepted_owner_decision(), _accepted_q001_values(), test_system_q001_owner_acceptance_rejects_options_fail_checks_despite_summary_ok(), test_system_q001_owner_acceptance_rejects_unknown_gap_evidence(), test_system_q001_owner_acceptance_requires_explicit_gap_evidence()

### Community 48 - "Community 48"
Cohesion: 0.29
Nodes (3): Production hardening tests for the paid-screen redesign (Phase 6).  Tests: fai, A partial JSON write (crash mid-write) must not be marked complete., TestPartialWrittenArtifact

### Community 49 - "Community 49"
Cohesion: 0.50
Nodes (3): Agent Runtime Roadmap — any LLM, every session, Quick charter links, Session load order (non-negotiable)

## Knowledge Gaps
- **147 isolated node(s):** `bool`, `int`, `WebSocket`, `Canonical working copy`, `Chronological trunk (GitHub `main`)` (+142 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `make_unit()` connect `Community 5` to `Community 48`, `Community 42`, `Community 37`?**
  _High betweenness centrality (0.043) - this node is a cross-community bridge._
- **Why does `PaidScreenUnit` connect `Community 37` to `Community 0`, `Community 5`?**
  _High betweenness centrality (0.041) - this node is a cross-community bridge._
- **Why does `bool` connect `Community 46` to `Community 29`, `Community 6`?**
  _High betweenness centrality (0.026) - this node is a cross-community bridge._
- **What connects `CHI404 measured latency summary for backtest replay (colo bare metal).`, `Fail loud if latency is outside blueprint-mandated replay band.`, `Return (order_ack_p99_ms, measured, source_label). Prefers new_send_to_ack over` to the rest of the system?**
  _225 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.14204545454545456 - nodes in this community are weakly interconnected._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.10526315789473684 - nodes in this community are weakly interconnected._
- **Should `Community 2` be split into smaller, more focused modules?**
  _Cohesion score 0.047619047619047616 - nodes in this community are weakly interconnected._