# Greptile zero-tolerance reset — 2026-06-20

**Branch:** `cursor/autoresearch-pr-c-phases-5-7` (PR #10)  
**Stack:** PR #8 MERGED · PR #9 MERGED · PR #10 OPEN  
**Policy:** owner zero-tolerance — unlimited iterations until 0 P1 + 0 P2 + 0 🔴 + 0 🟡

---

## Session reconciliation

Linear remote head `a3433804` consolidates all session fixes (no cherry-pick needed). Gate-order violation on `85eb27bd` closed by cavecrew dual-pass + fix batch before Greptile ping.

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

## Follow-up batch (post-a3433804 cavecrew)

| Item | Status |
|------|--------|
| `or_raise_or_raise` typos in hardening tests (6 sites) | **fixed** |
| Worker crash test expects ERROR rows not `[]` | **fixed** |
| `validate_paid_screen_ready_gate.py` fail-open list API | **fixed** — `or_raise` |
| `run_vectorbt_paid_screen.py` cached/post-write fail-open | **fixed** — `or_raise` |
| Batch artifact test silent validation | **fixed** — assert `== []` |
| Cockpit mock list API contract | **fixed** — mock returns `[]` |
| HBT dual validator surface | **documented** — comment in `hftbacktest_realism.py` |
| `test_fs_v1_context_loaded_once` npz_digest side effect | **fixed** — mock digest + FakeCtx attrs |

---

## cavecrew-reviewer (canonical head batch)

| Pass | 🔴 | 🟡 | merge-ready |
|------|----|----|-------------|
| Post-fix batch (6 files) | 0 | 0 | yes (code); Greptile pending |

---

## verify-run

```text
# minimum scope (research + backtest)
.\.venv\Scripts\python.exe -m pytest tests/research_pipeline/ tests/backtest_pipeline/ -q
exit 0 — 568 passed in ~420s

# hardening spot-check (post cavecrew fixes)
.\.venv\Scripts\python.exe -m pytest tests/test_paid_screen_hardening.py tests/test_paid_screen_performance.py::TestFeatureStoreBatchReuse::test_fs_v1_context_loaded_once_per_batch_key -q
exit 0 — 49 passed in ~13s
```

---

## Greptile status

| PR | Action |
|----|--------|
| #10 | Single `@greptileai` ping on new head **after** push (cancel stale pings on a3433804/85eb27bd/c333cff3) |

---

## Validation honesty

```text
merge-ready: no (Greptile #10 loop pending on new head)
scope-green: yes (568 research+backtest; 49 hardening spot-check)
scope: tests/research_pipeline/ + tests/backtest_pipeline/
verify-run: exit 0 — 568 passed + 49 passed (spot-check)
data-mode: offline
known-gaps: Greptile bot pending; full 904 paid_screen suite not re-run this batch
```
