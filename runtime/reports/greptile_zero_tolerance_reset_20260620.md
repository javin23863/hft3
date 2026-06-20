# Greptile zero-tolerance reset — 2026-06-20

**Branch:** `cursor/autoresearch-pr-c-phases-5-7` (PR #10)  
**Stack:** PR #8 MERGED · PR #9 MERGED · PR #10 OPEN  
**Policy:** owner zero-tolerance — unlimited iterations until 0 P1 + 0 P2 + 0 🔴 + 0 🟡

---

## Finding tracker (original 13)

| # | Source | Site | Status | Fix / evidence |
|---|--------|------|--------|----------------|
| 1 | Greptile P2 | `paid_screen_worker.py:210` | **fixed** | Worker exception returns per-unit `ERROR` results (fail-closed) |
| 2 | Greptile P2 | `paid_screen_batch.py:728` | **fixed** | LRU delta fold via `_fold_lru_profiler_delta()` on all exit paths |
| 3 | Greptile P2 | `vast_deploy_and_verify.ps1:64` | **fixed** | `ssh-keyscan` + pinned `known_hosts`; fail-closed on key change |
| 4 | Greptile P2 | `paid_screen_batch.py:38` | **fixed** | Removed redundant `_cache_get` branches |
| 5 | cavecrew 🟡 | `paid_screen_batch.py:274` | **fixed** | `npz_digest` in batching hash via `_resolve_npz_digest_for_unit` |
| 6 | cavecrew 🟡 | `paid_screen_batch.py:787` | **fixed** | Per-unit `unit_candidate` in artifact write loop |
| 7 | cavecrew 🟡 | `fs_v1_screen_path.py:95` | **fixed** | `leader_resolution_source` on context |
| 8 | cavecrew 🟡 | `fs_v1_screen_path.py:139` | **fixed** | `missing_leader_symbols` recorded; batch fail-closed for cross-asset |
| 9 | cavecrew 🟡 | `vectorbt_adapter.py:2738` | **fixed** | No double bar-shift; matrix `_shift_signal_to_executable_bar` only |
| 10 | cavecrew 🟡 | `paid_screen_cache.py:1326` | **fixed** | `put()` returns `False` on oversized reject |
| 11 | cavecrew 🟡 | `run_vectorbt_paid_screen_v2.py:682` | **fixed** | Post-exit queue drain + resume uses `or_raise` |
| 12 | PR #8 | statistical failed+missing double-count | **fixed** | `generation_gate_producers.py` partition |
| 13 | PR #8 | stop_no_improvement guard | **verified** | Already uses `cfg.stop_no_improvement_generations` |

**Fixed:** 12 code/doc · **Verified OK:** 1 · **Original open:** 0

---

## Follow-up batch (post-cb73bc87)

| Item | Status |
|------|--------|
| `validate_screening_artifact` / `or_raise` split | **fixed** — list API wraps `or_raise`; cockpit/resume migrated |
| 8 backtest_pipeline test failures | **fixed** — 342/342 backtest_pipeline pass in scope run |
| Latency baseline JSON validation | **fixed** — proxy_status, percentile order, extreme band bypass |
| Model router count 56→61 | **fixed** — dynamic `all_model_ids()` in tests |
| `compute_latency_probe_artifact_hash` missing | **fixed** — added to `hftbacktest_realism.py` |

---

## verify-run

```text
# scoped (796 tests — backtest_pipeline + research_pipeline + paid_screen)
.\.venv\Scripts\python.exe -m pytest tests/backtest_pipeline/ tests/research_pipeline/ tests/test_paid_screen_*.py tests/test_vectorbt_paid_screen_gate.py -q
exit 0 — 796 passed in ~571s
```

---

## cavecrew-reviewer (latest head)

| Pass | 🔴 | 🟡 | merge-ready |
|------|----|----|-------------|
| Post-3753e8b8 batch | 5 (validator migration — addressed in follow-up commit) | 6 | pending re-review after push |

---

## Greptile status

| PR | Action |
|----|--------|
| #10 | `@greptileai` ping after push of validator-migration commit |

---

## Validation honesty

```text
merge-ready: no (Greptile #10 loop pending; cavecrew re-review after final push)
scope-green: yes (796 scoped tests pass: backtest_pipeline + research_pipeline + paid_screen)
scope: tests/backtest_pipeline/ + tests/research_pipeline/ + tests/test_paid_screen_*.py + test_vectorbt_paid_screen_gate.py
verify-run: exit 0 — 796 passed in ~571s
data-mode: offline
known-gaps: Greptile #10 unlimited loop not started on latest SHA; full-repo pytest not run
```
