# Greptile zero-tolerance reset — 2026-06-20

**Branch:** `cursor/autoresearch-pr-c-phases-5-7` (PR #10)  
**Stack:** PR #8 MERGED · PR #9 MERGED · PR #10 OPEN  
**Policy:** owner zero-tolerance — unlimited iterations until 0 P1 + 0 P2 + 0 🔴 + 0 🟡  
**Head:** `cb73bc87` — `fix(pr-c): zero-tolerance findings — fail-closed paid_screen + policy`  
**Prior workflow:** [Zero-tolerance review workflow](53788621-2abe-4120-bf7b-86b1f0be92c1) — fixes landed in `cb73bc87`; this session verified code + pytest.

### PR #9 waive — SUPERSEDED

| Item | Detail |
|------|--------|
| Original waive | `PR-B Greptile waived-by-owner-20260620` — merged PR #9 with 4 P2 + 6 🟡 still open |
| Owner override | **2026-06-20 zero-tolerance** — no waives; all 11 findings fixed on PR #10 head |
| Status | **SUPERSEDED** — debt cleared in `cb73bc87`; do not treat PR #9 waive as precedent |

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

## verify-run (this session — 2026-06-20)

```text
# scoped zero-tolerance slice (.venv)
.\.venv\Scripts\python.exe -m pytest tests/test_paid_screen_batch.py tests/test_paid_screen_cache.py tests/test_paid_screen_matrix.py tests/test_paid_screen_v2_orchestrator.py tests/backtest_pipeline/test_feature_family_paid_gate.py tests/backtest_pipeline/test_fs_v1_vectorbt_path.py tests/research_pipeline/test_generation_gate_chain.py -q
exit 0 — 190 passed in 210.72s

# paid_screen-only (system python, no .venv)
python -m pytest tests/test_paid_screen_batch.py tests/test_paid_screen_cache.py tests/test_paid_screen_matrix.py tests/test_paid_screen_v2_orchestrator.py tests/backtest_pipeline/test_feature_family_paid_gate.py -q
exit 0 — 168 passed, 1 skipped in 170.90s

# fs_v1 path (system python)
python -m pytest tests/backtest_pipeline/test_fs_v1_vectorbt_path.py -q
exit 0 — 5 passed in 2.88s

# research_pipeline full (system python — BLOCKED)
python -m pytest tests/research_pipeline/ -q
exit 2 — 10 collection errors (ModuleNotFoundError: hftbacktest); use .venv for full suite

# backtest_pipeline full (prior session .venv — pre-existing failures)
.\.venv\Scripts\python.exe -m pytest tests/backtest_pipeline/ -q
exit 1 — 334 passed, 8 failed in ~310s (see table below)
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

## Gate-order compliance (owner correction 2026-06-20)

| Check | Result |
|-------|--------|
| **Prior push before review?** | **YES — VIOLATION** — `@greptileai` on PR #10 at 2026-06-20T09:08:08Z on head `acd5734c` before cavecrew dual-pass on subsequent head `cb73bc87` |
| Policy docs | **updated** — explicit **Build: cavecrew dual-pass → fix → verify → push → Greptile last** |
| cavecrew iter 1 (cb73bc87 diff) | 2🔴 6🟡 |
| cavecrew iter 2 (gate-order fixes) | 1🔴 4🟡 → fixed (import, holdout yaml lazy load, shared cross_asset detector, gate tests; reverted latency JSON scope creep) |
| cavecrew iter 3 | **0🔴 0🟡** (gate integration 24/24 pass) |
| verify-run (this session) | research_pipeline 222/224 pass; paid_screen 228 pass; backtest_pipeline 338/342 pass (4 pre-existing) |
| Greptile started this session | **no** (correct — after push only) |

---

## Greptile status

| PR | State | Greptile may start? |
|----|-------|---------------------|
| #9 | MERGED (waive **SUPERSEDED**) | N/A |
| #10 | OPEN | **After push** of gate-order commit + `@greptileai` on new head ONLY |

---

## Validation honesty

```text
merge-ready: no (Greptile #10 pending on new head; backtest_pipeline 4 pre-existing failures)
scope-green: no (research_pipeline 222/224; paid_screen 228 pass; backtest_pipeline 338/342)
scope: gate-order fix slice + tests/research_pipeline/ + paid_screen + tests/backtest_pipeline/
verify-run: research_pipeline exit 1 (222 pass / 2 fail), paid_screen exit 0 (228 pass), backtest_pipeline exit 1 (338 pass / 4 fail)
data-mode: offline
known-gaps: prior Greptile-before-cavecrew violation on acd5734c; 2 research_pipeline + 4 backtest_pipeline failures; Greptile re-ping after push
finding-count: gate-order fixes applied; cavecrew iter 3 clean on fix diff
```
