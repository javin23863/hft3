---
name: Autonomous Research Pipeline Gate Chain
overview: "Complete and harden the existing autoresearch loop (run_pipeline.py --autoresearch) with strict gates 0–8, separate regular WF vs WFC, honest generation summary/resume, performance path, tests, three-gen acceptance, local rg preflight, and mandatory Greptile PR GrepLoop. Documentation-only plan — implementation follows [AUTONOMOUS_RESEARCH_PIPELINE_DEVELOPER_ASSIGNMENT.md](../../docs/project/AUTONOMOUS_RESEARCH_PIPELINE_DEVELOPER_ASSIGNMENT.md)."
todos:
  - id: phase0-audit-rg
    content: "Phase 0 (§2): Run mandatory rg audit commands; produce requirement/file/function/test/artifact/active/gap table before any code edit"
    status: completed
  - id: phase0-locate-wfc
    content: "Phase 0 BLOCKER: Locate existing WFC impl (apps/workbench/src/robustness/wfc/gate.py, wfc_gate.yaml, tests, artifacts) — do NOT add second WFC until audit proves gap"
    status: completed
  - id: phase0-locate-regular-wf
    content: "Phase 0: Locate regular walk-forward impl (walk_forward.yaml, workbench, generation_summary) — prove distinct from WFC"
    status: completed
  - id: phase0-locate-greptile
    content: "Phase 0: Confirm Greptile PR GrepLoop entrypoints (docs/ai/GREPLOOP.md, .github, AGENTS.md); Codex is NOT acceptable substitute"
    status: completed
  - id: phase1-gate-chain-contract
    content: "Phase 1 (§4): Add/extend run_generation_gate_chain in generation_loop ownership with strict receipt schema and exact PASS comparisons"
    status: completed
  - id: phase2-gate0-ontology
    content: "Phase 2 (§5): Wire Gate 0 ontology admission (ontology_gate.py + ONTOLOGY_GATE_AGENT_SPEC) before VectorBT compute"
    status: completed
  - id: phase2-gate1-manifest
    content: "Phase 2 (§6): Gate 1 frozen candidate manifest (candidate_manifest.py, feature_recipe.py) with hash immutability"
    status: pending
  - id: phase2-gate2-vectorbt
    content: "Phase 2 (§7): Gate 2 VectorBT screen via optimized paid/worker/matrix path — no subprocess-per-unit"
    status: pending
  - id: phase2-gate3-surface
    content: "Phase 2 (§8): Gate 3 surface stability (surface_stability.py) with fail-closed missing cells/thresholds"
    status: pending
  - id: phase2-gate4-regular-wf
    content: "Phase 2 (§9): Gate 4 regular walk-forward (FIRST WF process) — holdout evaluate-only, no learning feedback"
    status: completed
  - id: phase2-gate5-wfc
    content: "Phase 2 (§10) BLOCKER: Gate 5 WFC (SECOND distinct process) — reuse existing evaluate_wfc_gate; full aligned parameter surface; Pearson+Spearman; no equity-curve/best-param substitute"
    status: completed
  - id: phase2-gate6-statistical
    content: "Phase 2 (§11): Gate 6 statistical/Monte Carlo gauntlet (allow_partial=False, robustness_producers + ROBUSTNESS_TESTING_SPEC)"
    status: pending
  - id: phase2-gate7-hft
    content: "Phase 2 (§12): Gate 7 HftBacktest realism (hft_campaign/, run_hft_campaign:true, per-candidate status)"
    status: pending
  - id: phase2-gate8-certification
    content: "Phase 2 (§13): Gate 8 final certification — FINAL_PASS only when all prior gates PASS; score cannot override"
    status: pending
  - id: phase3-generation-summary
    content: "Phase 3 (§14): Fix generation_summary — all candidates, elite=FINAL_PASS only, best_candidate from FINAL_PASS only"
    status: pending
  - id: phase3-learning-behavior
    content: "Phase 3 (§15): Karpathy autonomous learning — exploitation from FINAL_PASS only; no threshold lowering or WFC/HFT bypass"
    status: pending
  - id: phase3-review-memory
    content: "Phase 3 (§16): Extend review_memory with full gate outcomes including WFC Pearson/Spearman; advisory only"
    status: pending
  - id: phase4-completion
    content: "Phase 4 (§17): Honest .generation_complete — validate all receipts before marker; zero FINAL_PASS allowed"
    status: pending
  - id: phase4-resume
    content: "Phase 4 (§18): Deterministic resume — expanded config hash; reuse valid gates; rerun corrupt; no skip-on-marker"
    status: pending
  - id: phase5-vbt-performance
    content: "Phase 5 (§19): VectorBT performance — long-lived workers, matrix batch, shared loading; benchmark + projected campaign time"
    status: pending
  - id: phase5-hbt-performance
    content: "Phase 5 (§19): HftBacktest performance — prepared data reuse, fresh engine per scenario, bounded worker recycling"
    status: pending
  - id: phase6-gate-tests
    content: "Phase 6 (§20): Add planted PASS/FAIL tests for every gate including WFC independence, alignment, Pearson/Spearman, elite rules"
    status: pending
  - id: phase6-resume-tests
    content: "Phase 6 (§20): Tests for resume, completion marker timing, dedup recipe hashes, three-gen unattended"
    status: pending
  - id: phase7-three-gen-run
    content: "Phase 7 (§21): Run deterministic three-generation acceptance campaign; report rejects/FINAL_PASS/recipe changes"
    status: pending
  - id: phase8-rg-preflight
    content: "Phase 8 (§22): After each edit batch run bounded local rg negative/positive searches + git diff --check (max 3 iterations)"
    status: pending
  - id: phase9-greptile-loop
    content: "Phase 9 (§23) BLOCKER: Greptile PR GrepLoop ONLY — @greptileai, fix actionable findings, max 5 iterations; merge-ready requires current-head clean Greptile"
    status: pending
  - id: phase10-checklist
    content: "Phase 10 (§24): Complete final acceptance checklist — all 26 items must be true"
    status: pending
  - id: phase10-developer-table
    content: "Phase 10 (§25): Return required developer response table with evidence artifacts (not fixture-only claims)"
    status: pending
isProject: true
---

# Autonomous research pipeline — gate chain implementation plan

**Assignment authority:** [AUTONOMOUS_RESEARCH_PIPELINE_DEVELOPER_ASSIGNMENT.md](../../docs/project/AUTONOMOUS_RESEARCH_PIPELINE_DEVELOPER_ASSIGNMENT.md)

**Canonical command:** `python scripts/run_pipeline.py --autoresearch ...`

**Branch context:** `cursor/vast-vbt-workflow`

---

## Hard blockers (do not skip)

| Blocker | Requirement |
|---------|-------------|
| **WFC is second process** | Regular walk-forward (Gate 4) and Walk Forward Correlation (Gate 5) are **independent** gates. Neither PASS substitutes for the other. |
| **Reuse existing WFC** | Audit must locate `evaluate_wfc_gate` in [`apps/workbench/src/robustness/wfc/gate.py`](../../apps/workbench/src/robustness/wfc/gate.py), config [`apps/workbench/config/wfc_gate.yaml`](../../apps/workbench/config/wfc_gate.yaml), tests, and artifact schema **before** any new WFC code. |
| **Full parameter surface** | WFC correlates aligned cells across two surfaces by **parameter hash** — not equity curves, best params, or summary scores. |
| **Greptile required** | [Greptile PR GrepLoop](../../docs/ai/GREPLOOP.md) only. Codex/Copilot/Bugbot/local review **do not** satisfy merge-ready. |
| **Phase 0 before code** | Active-path audit table (§2) is mandatory first deliverable. |

---

## Path verification (repo scan 2026-06-20)

### Confirmed present

| Path | Role |
|------|------|
| [`scripts/run_pipeline.py`](../../scripts/run_pipeline.py) | Autoresearch CLI entry |
| [`packages/research_pipeline/generation_loop.py`](../../packages/research_pipeline/generation_loop.py) | Generation loop owner |
| [`packages/research_pipeline/generation_state.py`](../../packages/research_pipeline/generation_state.py) | Resume/state |
| [`packages/research_pipeline/generation_summary.py`](../../packages/research_pipeline/generation_summary.py) | Summary/elite selection |
| [`packages/research_pipeline/candidate_manifest.py`](../../packages/research_pipeline/candidate_manifest.py) | Gate 1 manifest |
| [`packages/research_pipeline/feature_recipe.py`](../../packages/research_pipeline/feature_recipe.py) | Recipe hash |
| [`packages/research_pipeline/review_memory.py`](../../packages/research_pipeline/review_memory.py) | Learning memory |
| [`packages/research_pipeline/src/robustness_producers.py`](../../packages/research_pipeline/src/robustness_producers.py) | Robustness wiring |
| [`packages/backtest_pipeline/src/ontology_gate.py`](../../packages/backtest_pipeline/src/ontology_gate.py) | Gate 0 |
| [`packages/backtest_pipeline/src/vectorbt_adapter.py`](../../packages/backtest_pipeline/src/vectorbt_adapter.py) | Gate 2 |
| [`packages/backtest_pipeline/src/surface_stability.py`](../../packages/backtest_pipeline/src/surface_stability.py) | Gate 3 |
| [`packages/backtest_pipeline/src/hft_campaign/`](../../packages/backtest_pipeline/src/hft_campaign/) | Gate 7 campaign |
| [`packages/backtest_pipeline/src/hftbacktest_realism.py`](../../packages/backtest_pipeline/src/hftbacktest_realism.py) | HFT realism |
| [`packages/backtest_pipeline/src/hft_backtest_builder.py`](../../packages/backtest_pipeline/src/hft_backtest_builder.py) | HFT builder |
| [`apps/workbench/config/walk_forward.yaml`](../../apps/workbench/config/walk_forward.yaml) | Regular WF config |
| [`apps/workbench/config/wfc_gate.yaml`](../../apps/workbench/config/wfc_gate.yaml) | WFC config |
| [`apps/workbench/src/robustness/wfc/gate.py`](../../apps/workbench/src/robustness/wfc/gate.py) | WFC `evaluate_wfc_gate` |
| [`docs/project/ONTOLOGY_GATE_AGENT_SPEC.md`](../../docs/project/ONTOLOGY_GATE_AGENT_SPEC.md) | Ontology authority |
| [`docs/project/VECTORBT_SCREENING_ENGINE_SPEC.md`](../../docs/project/VECTORBT_SCREENING_ENGINE_SPEC.md) | VectorBT authority |
| [`docs/project/HFTBACKTEST_REALISM_ENGINE_SPEC.md`](../../docs/project/HFTBACKTEST_REALISM_ENGINE_SPEC.md) | HFT authority |
| [`docs/project/ROBUSTNESS_TESTING_SPEC.md`](../../docs/project/ROBUSTNESS_TESTING_SPEC.md) | Robustness/WFC authority |
| [`docs/REVIEWER_CHARTER.md`](../../docs/REVIEWER_CHARTER.md) | Dual-pass review |
| [`docs/ai/GREPLOOP.md`](../../docs/ai/GREPLOOP.md) | Greptile procedure |
| [`docs/workbench/WALK_FORWARD_CAMPAIGNS.md`](../../docs/workbench/WALK_FORWARD_CAMPAIGNS.md) | WF + WFC campaign doc |
| [`docs/hft3_autonomous_pipeline_runbook.md`](../../docs/hft3_autonomous_pipeline_runbook.md) | Runbook |
| [`configs/research/autonomous_hft3.yaml`](../../configs/research/autonomous_hft3.yaml) | Autonomous config |
| [`vendor/vectorbt/VENDOR.lock`](../../vendor/vectorbt/VENDOR.lock) | VectorBT lock |
| [`vendor/hftbacktest/VENDOR.lock`](../../vendor/hftbacktest/VENDOR.lock) | HftBacktest lock |

### Audit must still confirm (existence ≠ wired to `--autoresearch`)

- WFC call site active in `run_pipeline.py --autoresearch` path
- Regular WF receipt schema → `regular_walk_forward_gate.json`
- WFC receipt schema → `walk_forward_correlation_gate.json`
- Martyn Tinsley / transcript reference location (rg per §2)
- Greptile GitHub integration trigger in `.github/` (rg per §2)
- `run_generation_gate_chain` — **not yet present** (expected new work in Phase 1)

---

## Phase map

| Phase | Assignment § | Deliverable |
|-------|----------------|-------------|
| **0** | §2 | Active-path audit table; WFC vs regular WF disambiguation |
| **1** | §4 | `run_generation_gate_chain` contract + receipt schema |
| **2** | §5–§13 | Gates 0–8 wired to autoresearch with artifact paths |
| **3** | §14–§16 | generation_summary, learning behavior, review_memory |
| **4** | §17–§18 | Honest completion + deterministic resume |
| **5** | §19 | VectorBT + HftBacktest performance benchmark |
| **6** | §20 | Full automated test matrix (planted PASS/FAIL) |
| **7** | §21 | Three-generation unattended acceptance run |
| **8** | §22 | Local rg preflight loop after edit batches |
| **9** | §23 | Greptile PR GrepLoop to clean current-head review |
| **10** | §24–§25 | Final checklist + developer response table |

---

## Phase 0 — Active-path audit (§2) — MUST complete before code

```bash
rg -n "run_autoresearch_loop|run_single_generation|propose_next_candidates" \
  scripts packages apps tests
rg -n "walk.forward|walk_forward|Walk Forward|WFC|Pearson|Spearman|correlation" \
  apps packages docs tests
rg -n "Martyn Tinsley|Walk Forward Correlation|youtube|YouTube|transcript" \
  docs apps packages tests
rg -n "allow_partial|robustness_pass|elite|best_candidate|hft_replay_status" \
  packages/research_pipeline apps/workbench
rg -n "greptile|greptileai|GrepLoop|PR GrepLoop" \
  .github docs AGENTS.md
```

Output table columns: Requirement | Existing file | Function/class | Test | Artifact | Active in `--autoresearch`? | Complete? | Gap | Minimal change

**Exit criteria:** Audit published; WFC reuse decision documented; no code until signed off.

---

## Phase 1 — Gate chain contract (§4)

- Owner: `packages/research_pipeline/generation_loop.py` (or justified helper)
- Function: `run_generation_gate_chain(...)` — **not** named `GrepLoop`
- Strict PASS: `passed == required`, `failed == 0`, `missing == 0`
- Remove permissive patterns (`robustness_pass is not False`, etc.)

---

## Phase 2 — Gates 0–8 (§5–§13)

| Gate | Receipt path | Key invariant |
|------|--------------|---------------|
| 0 Ontology | `generation_<N>/gates/<id>/ontology_gate.json` | Before VectorBT compute |
| 1 Manifest | (manifest + hash gate) | Immutable post-freeze |
| 2 VectorBT | `.../vectorbt_gate.json` | Matrix/worker path only |
| 3 Surface | `.../surface_stability_gate.json` | No post-hoc grid |
| 4 Regular WF | `.../regular_walk_forward_gate.json` | **First** WF process |
| 5 WFC | `.../walk_forward_correlation_gate.json` | **Second** process; Pearson+Spearman on aligned surface |
| 6 Statistical | `.../statistical_robustness_gate.json` | `allow_partial=False` |
| 7 HftBacktest | `.../hftbacktest_gate.json` | Per-candidate; full stages |
| 8 Final | certification receipt | `FINAL_PASS` only if all PASS |

---

## Phase 3 — Summary + learning (§14–§16)

- [`generation_summary.py`](../../packages/research_pipeline/generation_summary.py): `elite = final_status == "FINAL_PASS"`
- [`feature_family_proposals.py`](../../packages/research_pipeline/feature_family_proposals.py) / [`elite_refinement.py`](../../packages/research_pipeline/elite_refinement.py): exploitation from FINAL_PASS only
- [`review_memory.py`](../../packages/research_pipeline/review_memory.py): record WFC Pearson/Spearman; advisory only

---

## Phase 4 — Completion + resume (§17–§18)

- No `.generation_complete` until all receipts validate
- Config hash covers WFC windows, thresholds, HFT stages, gate versions
- Resume: reuse valid gates; rerun corrupt; never skip on marker alone

---

## Phase 5 — Performance (§19)

VectorBT: no `run_pipeline.py` subprocess per unit; matrix batch; shared feature load.

HftBacktest: prepared data + feature timeline reuse; fresh engine per scenario.

Deliverable: identical-scope benchmark + projected full-campaign time.

---

## Phase 6 — Tests (§20)

Minimum new/extended test files under `tests/` covering:

- Gate ordering (ontology before VectorBT)
- WFC independence from regular WF
- Parameter-hash alignment, grid mismatch rejection, constant-vector rejection
- Partial robustness / HFT not_run cannot elite
- Resume + completion marker honesty
- Three-generation unattended (integration)

Each gate: planted PASS + planted FAIL.

---

## Phase 7 — Three-gen acceptance (§21)

Deterministic config; unattended run through gen 0→1→2.

Report: rejects by gate, FINAL_PASS count, parent-child recipe diffs (real feature dimension change required), dedup counts, stop reason.

---

## Phase 8 — Local rg preflight (§22)

After every edit batch (max 3 loops):

**Negative:**

```bash
rg -n "robustness_pass is not False|hft_replay_status.*not in" packages apps tests
rg -n "allow_partial\s*=\s*True|run_hft_campaign:\s*false|hft_stages:\s*\[0\]" packages apps config tests
rg -n "elite.*vectorbt_pass|best_candidate.*vectorbt" packages/research_pipeline
rg -n "WFC.*optional|walk.forward.correlation.*not_run" packages apps config tests
```

**Positive:**

```bash
rg -n "regular_walk_forward_gate|walk_forward_correlation_gate" packages apps tests
rg -n "pearson|spearman|parameter_universe_hash|aligned_parameter_hashes" packages apps tests
rg -n "FINAL_PASS|run_generation_gate_chain|hftbacktest_gate" packages apps tests
git diff --check
```

---

## Phase 9 — Greptile PR GrepLoop (§23)

**Greptile ONLY.** See [GREPLOOP.md](../../docs/ai/GREPLOOP.md).

1. `gh pr view --json number,headRefName,headRefOid,url`
2. `git push`
3. `gh pr comment <PR> --body "@greptileai"`
4. Fetch reviews/comments via `gh pr view` + `gh api`
5. Fix all actionable findings
6. Re-run: local rg, dual-pass reviewer, tests, `git diff --check`
7. Push + `@greptileai` again
8. Stop when: Greptile reviewed **current head SHA** + zero actionable findings + verification green (max 5 iterations)

**merge-ready: no** until Greptile gate passes or owner waives.

---

## Phase 10 — Final checklist + developer table (§24–§25)

- Complete all 26 checklist items in assignment §24
- Return §25 table only — evidence from canonical `--autoresearch` run, not fixture existence

---

## Three loops (§3) — naming discipline

| Loop | Name | Runtime function |
|------|------|------------------|
| A | Autonomous research | `generation_loop.py` |
| B | Local rg preflight | bounded `rg` (Phase 8) |
| C | Greptile PR GrepLoop | `@greptileai` on PR (Phase 9) |

**Never** name runtime gate orchestration `GrepLoop`.

---

## Related specs (quick index)

- [Assignment doc](../../docs/project/AUTONOMOUS_RESEARCH_PIPELINE_DEVELOPER_ASSIGNMENT.md)
- [Autoresearch gap matrix](../../docs/project/AUTORESEARCH_GAP_MATRIX.md)
- [Phase contracts](../../docs/project/PHASE_CONTRACTS.md)
- [Validation matrix](../../docs/project/VALIDATION_MATRIX.md)
- [AGENTS.md](../../AGENTS.md) — delegation + merge-ready criteria
