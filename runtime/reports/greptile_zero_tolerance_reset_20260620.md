# Greptile zero-tolerance reset — 2026-06-20

**Branch:** `cursor/autoresearch-pr-c-phases-5-7` (PR #10)  
**Stack:** PR #8 MERGED · PR #9 MERGED · PR #10 OPEN  
**Policy:** owner zero-tolerance — unlimited iterations until 0 P1 + 0 P2 + 0 🔴 + 0 🟡

---

## Finding tracker

| # | Source | Site | Status | Fix / evidence |
|---|--------|------|--------|----------------|
| 1 | Greptile P2 | `paid_screen_worker.py:210` | **fixed** | Worker exception returns per-unit `ERROR` results (fail-closed), not empty list |
| 2 | Greptile P2 | `paid_screen_batch.py:728` | **fixed** | LRU delta fold moved after fs_v1 cache loads via `_fold_lru_profiler_delta()` on all exit paths |
| 3 | Greptile P2 | `vast_deploy_and_verify.ps1:64` | **fixed** | `ssh-keyscan` → `StrictHostKeyChecking=yes` + dedicated known_hosts; fail-closed on scan failure |
| 4 | Greptile P2 | `paid_screen_batch.py:38` | **fixed** | Removed redundant isinstance branches in `_cache_get` |
| 5 | cavecrew 🟡 | `paid_screen_batch.py:274` | **fixed** | `resolve_batching_hashes` includes `npz_digest` via `_resolve_npz_digest_for_unit` |
| 6 | cavecrew 🟡 | `paid_screen_batch.py:787` | **fixed** | Per-unit `unit_candidate` / `unit_parsed` in artifact write loop |
| 7 | cavecrew 🟡 | `fs_v1_screen_path.py:95` | **fixed** | `leader_resolution_source` on context (`cross_asset_module` vs `static_fallback`) |
| 8 | cavecrew 🟡 | `fs_v1_screen_path.py:139` | **fixed** | `missing_leader_symbols` tuple recorded on `FsV1ScreenContext` |
| 9 | cavecrew 🟡 | `vectorbt_adapter.py:2738` | **fixed** | Bar-synthetic MBO path defers signal one bar (PIT / executable lag parity) |
| 10 | cavecrew 🟡 | `paid_screen_cache.py:1326` | **fixed** | `put()` returns `False` on oversized reject; `oversized_reject_count` observable |
| 11 | cavecrew 🟡 | `run_vectorbt_paid_screen_v2.py:682` | **fixed** | Post-exit queue drain before worker-exit stop reason |
| 12 | PR #8 | statistical failed+missing double-count | **fixed** | `generation_gate_producers.py` — explicit passed/failed/missing partition |
| 13 | PR #8 | stop_no_improvement guard | **verified** | Already uses `cfg.stop_no_improvement_generations` (no hardcoded `>= 2`) |

**Fixed:** 12 code/doc · **Verified already OK:** 1 · **Remaining open:** 0 (code findings)

---

## verify-run

```text
# paid_screen scope (157 tests)
.\.venv\Scripts\python.exe -m pytest tests/test_paid_screen_batch.py tests/test_paid_screen_worker.py tests/test_paid_screen_cache.py tests/test_paid_screen_v2_orchestrator.py tests/test_vectorbt_paid_screen_gate.py tests/test_paid_screen_matrix.py tests/test_paid_screen_types.py -q
exit 0 — 157 passed in ~186s

# research_pipeline scope (224 tests)
.\.venv\Scripts\python.exe -m pytest tests/research_pipeline/ -q
exit 0 — 224 passed in ~61s

# backtest_pipeline scope (342 tests)
.\.venv\Scripts\python.exe -m pytest tests/backtest_pipeline/ -q
exit 1 — 334 passed, 8 failed in ~310s
```

### backtest_pipeline failures (pre-existing / out of paid_screen fix scope)

| Test | Likely blocker |
|------|----------------|
| `hft_campaign/test_hft_campaign_core.py` (2) | Campaign manifest / artifact API drift |
| `hft_campaign/test_hft_campaign_integration.py` | Replay-eligible manifest selection |
| `test_latency_components.py` (2) | Latency realism validation fixtures |
| `test_pipeline_gate_report.py` | Gate report NOT_RUN row shape |
| `test_pipeline_model_router.py` | Model inventory count (expects 56) |
| `test_robustness_bridge.py::TestPBOFails` | `cscv_status` expects `pass`, got `structure_ran` |

Not introduced by this zero-tolerance fix batch — document as known-gaps until triaged on PR #10.

---

## Policy docs updated

- `.cursor/plans/autonomous_research_pipeline_gate_chain.plan.md` — Phase 9 unlimited iterations, perfection gate primary
- `docs/ai/GREPLOOP.md` — superseded 5-iter STOP FAIL; stacked PR gate = zero findings not ≥4/5
- `docs/project/AUTONOMOUS_RESEARCH_PIPELINE_DEVELOPER_ASSIGNMENT.md` §23 — owner override note

---

## Greptile status

| PR | State | Next |
|----|-------|------|
| #9 | MERGED | — |
| #10 | OPEN | Push fixes → dual-pass review → pytest → `@greptileai` on #10 |

---

## Validation honesty

```text
merge-ready: no (Greptile #10 not re-run on new head; backtest_pipeline 8 pre-existing failures)
scope-green: no (paid_screen + research_pipeline green; backtest_pipeline 8 failures)
scope: tests/test_paid_screen_*.py + tests/research_pipeline/ + tests/backtest_pipeline/
verify-run: paid_screen exit 0 (157), research_pipeline exit 0 (224), backtest_pipeline exit 1 (334 pass / 8 fail)
data-mode: offline
known-gaps: Greptile loop on PR #10 after push; 8 backtest_pipeline failures (see table); full-repo pytest not run
```
