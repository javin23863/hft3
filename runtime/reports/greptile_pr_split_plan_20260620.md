# Greptile PR split plan — PR #7 (`cursor/vast-vbt-workflow`)

**Date:** 2026-06-20
**Problem:** Greptile hard limit 100 changed files; PR #7 has **147** files (+26k / −821 LOC).
**Goal:** Three stacked PRs, each **<80 files**, preserving merge order and test gates.

---

## Summary split

| PR | Title (proposed) | Base | Est. files | Est. LOC | Merge order |
|----|------------------|------|------------|----------|-------------|
| **PR-A** | `feat(research): autoresearch gate chain Phases 0–4` | `main` | ~62 | ~12k | 1 (first) |
| **PR-B** | `feat(vbt): paid-screen v2 perf + Vast deploy contract` | PR-A | ~58 | ~14k | 2 |
| **PR-C** | `feat(research): Phases 5–7 + agent docs + promotion gate fix` | PR-B | ~35 | ~2k | 3 (current tail) |

---

## PR-A — Autoresearch gate chain core (Phases 0–4)

**Commits (chronological, `main..fc6e666b` subset):**

| Commit | Subject |
|--------|---------|
| `46fc0c3f` | docs(autoresearch): Phase 0 active-path audit artifact |
| `38beef63` | docs(project): add autonomous pipeline developer assignment and plan |
| `4e406c79` | docs(agents): harden Fable then Ponytail as first-load context |
| `8ec0afcb` | feat(research): add strict generation gate chain contract |
| `3aa4741a` | feat(research): add generation gate producers (Phase 2 slice A/C) |
| `e411b2a0` | feat(research): wire gate chain into generation_loop (Phase 2 slice B) |
| `136a0556` | feat(research): wire gates 2/3/6 producers (Phase 2D) |
| `1940bd7e` | feat(research): wire gates 1/7/8 certification (Phase 2E) |
| `8d134957` | feat(research): Phase 3 summary, learning, memory (§14-16) |
| `c90bd870` | feat(research): Phase 4 honest completion and deterministic resume |
| `f5c08439` | fix(research): fail-closed resume without checkpoint candidates |
| `6c56b487` | docs(pipeline): unify VBT→HBT→robustness→lifecycle chronological pipeline |
| `b52bb548` | fix(tests): align model registry slug counts with inventory |
| `a418e90b` | fix(tests): disable fs_v1 path in missing-OHLCV filter test |

**Primary paths (~62 files):**

```
.cursor/plans/autonomous_research_pipeline_gate_chain.plan.md
.cursor/rules/00-fable-mindset.mdc
.cursor/rules/01-ponytail-mindset.mdc
.cursor/rules/ponytail.mdc
.cursor/rules/graph-before-code.mdc
.cursor/rules/hft3-standing-workflow.mdc
.cursor/rules/vault-gate-mandatory.mdc
AGENTS.md, CLAUDE.md, README.md
docs/ai/ONBOARDING.md, docs/ai/PONYTAIL.md
docs/project/AUTONOMOUS_RESEARCH_PIPELINE_DEVELOPER_ASSIGNMENT.md
docs/vault/AGENT_RUNTIME_ROADMAP.md, docs/vault/FABLE_MINDSET.md, docs/vault/README.md
docs/vault/UNIFIED_RESEARCH_PIPELINE.md, docs/vault/RESEARCH_ENTRYPOINTS.md
packages/research_pipeline/generation_gate_chain.py          (new)
packages/research_pipeline/generation_gate_producers.py      (new)
packages/research_pipeline/generation_loop.py
packages/research_pipeline/generation_state.py
packages/research_pipeline/generation_summary.py
packages/research_pipeline/elite_refinement.py
packages/research_pipeline/feature_family_proposals.py
packages/research_pipeline/candidate_manifest.py
packages/research_pipeline/review_memory.py
packages/backtest_pipeline/src/research_pipeline_stages.py   (new)
packages/backtest_pipeline/src/hftbacktest_realism.py
apps/cockpit/backend/aggregate/* (lifecycle hooks only)
apps/workbench/src/robustness/pack.py
runtime/reports/autoresearch_active_path_audit_20260620.md   (new)
tests/research_pipeline/test_generation_gate_chain.py        (new)
tests/research_pipeline/test_generation_gate_integration.py
tests/research_pipeline/test_generation_phase3.py
tests/research_pipeline/test_generation_loop.py
tests/research_pipeline/test_feature_family_e2e_smoke.py
tests/test_workbench/test_wfc_gate.py
tests/test_vectorbt_paid_screen_gate.py (slug-count slice only)
```

**Verify gate:** `pytest tests/research_pipeline/ -q` (Phases 0–4 subset ~180 tests)

---

## PR-B — Paid-screen v2 perf + Vast deploy contract

**Base:** PR-A merged (or branch `pr-a/autoresearch-gates`).

**Commits:**

| Commit | Subject |
|--------|---------|
| `8af6be37` … `0ddfce2a` | VBT paid-screen v2 incident recovery, matrix sl/tp, orchestrator lifecycle |
| `1d06cdd5` | chore(repo): cleanup incident scratch and update REPO_STATE |
| `a9f040de` | refactor(vbt): retire paid-screen v1 subprocess orchestrator |
| `58642cab` | fix(verify): resolve workstation Python for agent verify |
| `252d5616` | feat(vbt): wire primary_fs_v1 and cross_asset_futures on v2 paid-screen |
| `f57c59b7` | test(vbt): add v2 feature-family consumption integration test |
| `3cfab691` | chore(gate): allow paid_screen_gate for Vast full run |
| `94e32535` | fix(vbt): pilot fs_v1 vocab and gate hash stamping |
| `4e62e556` | feat(ops): ponytail charter and Vast D3/D4 launch scripts |
| `0618cc6d` … `60fb7384` | feat(vast): Plan v3 deploy contract, NPZ filter, abort wiring |
| `5407cc4f` | perf(vbt): NPZ pre-filter events before model cartesian |
| `a99cf228` | chore(vast): declaration 72950 host-filtered units |
| `0ddfce2a` | fix(vbt): wire paid-compute promotion gate to official stats |

**Primary paths (~58 files):**

```
packages/backtest_pipeline/src/paid_screen_batch.py        (new)
packages/backtest_pipeline/src/paid_screen_cache.py        (new)
packages/backtest_pipeline/src/paid_screen_matrix.py       (new)
packages/backtest_pipeline/src/paid_screen_profiling.py    (new)
packages/backtest_pipeline/src/paid_screen_types.py        (new)
packages/backtest_pipeline/src/paid_screen_worker.py       (new)
packages/backtest_pipeline/src/fs_v1_screen_path.py
packages/backtest_pipeline/src/feature_plane.py
packages/backtest_pipeline/src/feature_family_status.py
packages/backtest_pipeline/src/promotion_gate.py           (evaluate_failures slice)
packages/backtest_pipeline/src/vectorbt_adapter.py         (matrix + gate wiring pre-WF fix)
docs/project/PAID_SCREEN_*.md (6 new specs)
docs/project/VBT_PAID_SCREEN_*.md
docs/human/RESEARCH_SYSTEM_EXECUTION_ORDER.md
docs/REPO_STATE.md
.gitignore
runtime/_deprecated_vast_incident_20260619/*
runtime/_launch_repro230.sh, runtime/manifest_check.py, runtime/unit_results_check.py
runtime/vbt_*.sh, runtime/vbt_*.py
runtime/reports/paid_screen_ready_gate.json
runtime/reports/plan_drift_review.json
runtime/reports/vbt_full_run_declaration.json
scripts/vast_deploy_and_verify.ps1 (if on branch)
tests/test_paid_screen_batch.py
tests/test_paid_screen_matrix.py
tests/test_paid_screen_performance.py
tests/test_plan_drift_review.py
tests/backtest_pipeline/test_feature_family_paid_gate.py
tests/research_pipeline/test_autoresearch_vectorbt_performance.py
```

**Verify gate:** `pytest tests/test_paid_screen_batch.py tests/test_paid_screen_matrix.py tests/test_plan_drift_review.py -q`

---

## PR-C — Phases 5–7 + promotion gate math fix (current tail)

**Base:** PR-B merged.

**Commits:**

| Commit | Subject |
|--------|---------|
| `aa44836b` | feat(research): Phase 5 VectorBT matrix path and perf counters |
| `fec7be00` | test(research): Phase 6 expanded gate planted tests |
| `fc6e666b` | test(research): Phase 7 three-gen acceptance dry-run |
| *(uncommitted)* | fix(vbt): walk-forward OOS expectancy + full PromotionGate hydration |

**Primary paths (~35 files):**

```
packages/research_pipeline/generation_loop.py              (Phase 5 autoresearch filter hook)
packages/backtest_pipeline/src/vectorbt_adapter.py         (WF OOS gate fix)
tests/test_paid_screen_batch.py                            (gate fixture updates)
tests/test_vectorbt_adapter.py                             (pilot_gate_evaluation contract)
tests/research_pipeline/test_generation_phase6*.py         (if split from Phase 6 commit)
tests/research_pipeline/test_three_gen_acceptance.py         (if present)
runtime/reports/autoresearch_performance_audit_20260620.md
runtime/reports/autoresearch_three_gen_acceptance_20260620.md
runtime/reports/greptile_pr_split_plan_20260620.md          (this file)
```

**Verify gate:** `pytest tests/research_pipeline/ -q` (full ~230 tests)

---

## Execution notes for owner

1. **Do not force-push PR #7.** Create `pr-a/autoresearch-gates` from cherry-picked PR-A commits, open new PR, merge, then rebase PR-B and PR-C.
2. **Cherry-pick conflicts:** `generation_loop.py`, `vectorbt_adapter.py`, and `tests/research_pipeline/*` touch multiple phases — resolve by taking PR-A version first, then layer PR-B matrix imports, then PR-C autoresearch hook.
3. **Greptile re-trigger:** Only after PR-C file count ≤80. Current monolithic PR #7 stays blocked at 147 until split.
4. **Cockpit aggregate churn:** `apps/cockpit/backend/aggregate/pipeline.py` (+878) is the largest single diff — keep entirely in PR-A (Stage registry) or PR-B (screening manifest readers); do not split across three PRs.
5. **Deprecated runtime scripts:** Keep `runtime/_deprecated_vast_incident_20260619/*` in PR-B only (ops artifact, not gate chain).

---

## File-count sanity check (from `gh pr view 7 --json files`)

| Bucket | Count |
|--------|-------|
| PR-A (research gates + docs + cockpit stage hooks) | ~62 |
| PR-B (paid-screen v2 + vast + promotion_gate pre-fix) | ~58 |
| PR-C (Phase 5–7 + gate math fix + reports) | ~27 |
| **Total** | **147** |

Each bucket **<80** ✓

---

## merge-ready status (this follow-up)

| Gate | Status |
|------|--------|
| Reviewer 🔴 | **0** (2 prior 🔴 fixed: WF OOS labeling + PromotionGate bypass) |
| Reviewer 🟡 | 4 metadata nits (wf_consistency skipped list — addressed in uncommitted fix) |
| `pytest tests/research_pipeline/ -q` | **230 passed** |
| Greptile | **Blocked** until split (147 > 100) |
| **merge-ready** | **no** — Greptile blocked + full scope-green not run |
