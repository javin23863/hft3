# Autoresearch active-path audit — Phase 0 (2026-06-20)

**Branch:** `cursor/vast-vbt-workflow`  
**Assignment:** `docs/project/AUTONOMOUS_RESEARCH_PIPELINE_DEVELOPER_ASSIGNMENT.md` §2  
**Canonical command:** `python scripts/run_pipeline.py --autoresearch ...`  
**Scope:** Audit only — no gate-chain implementation in this session.

## Executive summary

The `--autoresearch` CLI and generation loop exist and run VectorBT → workbench `run_campaign` robustness → optional HFT. **No strict gate chain** (`run_generation_gate_chain`, per-gate receipts, `FINAL_PASS`) is present in `packages/` or `apps/`. Regular walk-forward (B4 periods) and Walk Forward Correlation (`evaluate_wfc_gate`) are **implemented in workbench** but **bundled inside one `run_campaign` call**; autoresearch collapses outcomes into a single permissive `robustness_pass` and does **not** treat them as independent gates. WFC emits Pearson/Spearman in workbench artifacts but **lacks** several assignment §10 receipt fields (`parameter_universe_hash`, `aligned_parameter_hashes`, structured scatter/quadrant rows). Ontology admission is **not** wired before VectorBT compute on the autoresearch path. Greptile procedure is documented in `docs/ai/GREPLOOP.md`; **no** `.github` Greptile workflow was found (Codex PR review only).

---

## Audit table

| Requirement | Existing file | Existing function/class | Existing test | Existing artifact | Active in `run_pipeline.py --autoresearch`? | Complete? | Gap | Minimal required change |
|-------------|---------------|-------------------------|---------------|-------------------|-----------------------------------------------|-----------|-----|-------------------------|
| **Regular walk-forward** | `apps/workbench/config/walk_forward.yaml`; `decision_engine/python/src/walk_forward.py`; `apps/workbench/src/run/campaign_runner.py` | `load_walk_forward_config`; B4 `ValidationPeriod` evaluation inside `run_campaign` | `tests/test_workbench/test_campaign_runner.py`; workbench period tests | `research_cards/workbench_runs/<campaign_id>/periods/<Stage>/`; `summary.json` → `walk_forward` | **Partial** — invoked only via `make_default_robustness_fn` → `run_campaign` on top-K promoted | **No** | No `regular_walk_forward_gate.json`; not independent of WFC; holdout exclusion only in summary scoring, not gate receipts | Wire Gate 4 producer; write `generation_<N>/gates/<id>/regular_walk_forward_gate.json`; require `== "PASS"` in gate chain |
| **Walk Forward Correlation (2nd process)** | `apps/workbench/src/robustness/wfc/gate.py`; `apps/workbench/config/wfc_gate.yaml`; `docs/workbench/WALK_FORWARD_CAMPAIGNS.md` | `evaluate_wfc_gate`; `write_wfc_artifacts` | `tests/test_workbench/test_wfc_gate.py`; `test_wfc_campaign_integration.py` | `.../wfc/wfc_summary.json`; scatter PNGs | **Partial** — same `run_campaign` bundle; autoresearch reads `wfc_status` into combined `robustness_pass` | **No** | Conflated with regular WF + generic `robustness_passed`; no `walk_forward_correlation_gate.json`; `evaluate_double_wf` exists but **not** called from `campaign_runner` | Separate Gate 5 receipt; autoresearch must not substitute regular-WF PASS for WFC PASS |
| **Parameter-surface construction** | `apps/workbench/src/run/campaign_runner.py`; `workbench/config/models.yaml` (`parameter_bounds`) | Matrix row builder before WFC (save_matrix_rows) | `test_wfc_campaign_integration.py` | `wfc/param_matrix.parquet` (when bounds present) | **Conditional** — only models with `parameter_bounds` and non-trial `run_campaign` | **Partial** | Not run for all autoresearch candidates; pilot scope may skip full grid | Require full predeclared surface per gate spec before WFC/surface gates |
| **Surface alignment** | `apps/workbench/src/robustness/wfc/gate.py` (`_aggregate_by_parameter`, `parameter_hash`) | `evaluate_wfc_gate` row alignment by `parameter_hash` | `test_wfc_gate.py` (grid/hash cases) | Matrix rows keyed by `parameter_hash` | **Same as WFC** | **Partial** | Missing `parameter_universe_hash`, `aligned_parameter_hashes`, `missing_from_a/b` in receipts (rg: **zero** hits in repo code) | Extend WFC artifact schema + gate receipt per assignment §10 |
| **Pearson computation** | `apps/workbench/src/robustness/wfc/gate.py` | `evaluate_wfc_gate` → `_median_correlation(..., pearsonr)` | `test_wfc_gate.py`; `test_robustness_bridge.py` | `wfc_summary.json` → `pearson` | **Indirect** via `run_campaign` only | **Partial** | Not exposed as standalone gate status in autoresearch | Copy into `walk_forward_correlation_gate.json` with authority refs |
| **Spearman computation** | `apps/workbench/src/robustness/wfc/gate.py` | `evaluate_wfc_gate` → `_median_correlation(..., spearmanr)` | `test_wfc_gate.py`; `test_robustness_bridge.py` | `wfc_summary.json` → `spearman` | **Indirect** | **Partial** | Same as Pearson | Same as Pearson |
| **Surface-stability testing** | `packages/backtest_pipeline/src/surface_stability.py`; `vectorbt_adapter.py` | `compute_surface_stability` | `tests/test_vectorbt_adapter.py` (surface fields) | `screening_artifact.json` → `surface_stability_metrics` per promoted row | **Partial** — during VectorBT screen, not post-gate receipt | **No** | No `surface_stability_gate.json`; formula-missing paths fail screening but not strict gate chain | Gate 3 receipt + chain enforcement |
| **Bootstrap / Monte Carlo** | `packages/research_pipeline/src/robustness_producers.py`; `robustness_bridge.py` | `bootstrap_ci`; WFC internal `_bootstrap_ci` | `tests/backtest_pipeline/test_robustness_bridge.py` | `bootstrap_ci_or_not_run` in bridge output | **No** on autoresearch path — bridge not called from `generation_loop` | **No** | Autoresearch uses `run_campaign` summary only; certifying `allow_partial=False` not used | Gate 6 via `robustness_bridge` / producers with `allow_partial=False` |
| **DSR** | `robustness_producers.py` → `deflated_sharpe_for_cell`; `robustness_bridge.py` → `_run_dsr` | `compute_robustness_evidence` | `test_robustness_bridge.py` | `dsr_or_not_run` | **No** on autoresearch path | **No** | Same as bootstrap | Wire statistical gauntlet Gate 6 |
| **CSCV / PBO** | `robustness_producers.py` → `cscv_pbo`; `robustness_bridge.py` → `_run_cscv_pbo` | `compute_robustness_evidence` | `test_robustness_bridge.py` | `pbo_or_not_run`, `cscv_count_or_not_run` | **No** on autoresearch path | **No** | Same | Gate 6 |
| **Holm / BH** | `robustness_producers.py` → `holm_bh_correction`; `robustness_bridge.py` → `_run_holm_bh` | `compute_robustness_evidence` | `test_robustness_bridge.py` | `holm_bh_or_not_run` | **No** on autoresearch path | **No** | Same | Gate 6 |
| **Fee / slippage / latency stress** | `robustness_producers.py`; `robustness_bridge.py`; `hft_campaign/manifest.py` (scenario variants) | `fee_stress_for_cell`, `slippage_stress_for_cell`, `latency_stress_for_cell` | `test_robustness_bridge.py` | `*_stress_or_not_run`; HFT scenario stress names | **No** on autoresearch path (stress producers); HFT stress only if campaign enabled | **No** | Autoresearch default skips HFT; robustness_fn does not invoke bridge stress | Gate 6 + Gate 7 stress scenarios when certifying |
| **HftBacktest campaign** | `packages/backtest_pipeline/src/hft_campaign/runner.py`; `generation_loop.py` | `run_hftbacktest_campaign`; `_run_hft_campaign` | `tests/backtest_pipeline/hft_campaign/` | `generation_<N>/hft_campaign/` when enabled | **Optional** — `config/autoresearch/default.yaml` → `run_hft_campaign: false`, `hft_stages: [0]` | **No** | Campaign-level status applied to all candidates; not per-candidate `hftbacktest_gate.json` | Gate 7 per candidate; certifying config `run_hft_campaign: true`, full stages |
| **Ontology gate** | `packages/backtest_pipeline/src/ontology_gate.py`; `scripts/run_ontology_gate.py` | `run_gate` | `tests/backtest_pipeline/test_ontology_gate.py` | Ad-hoc `runtime/reports/ontology_gate_*.json` (validation scripts) | **No** — `generation_loop.py` has **zero** `ontology_gate` imports | **No** | No `generation_<N>/gates/<id>/ontology_gate.json`; compute runs without admission | Gate 0 before VectorBT in `run_single_generation` |
| **Greptile PR review loop** | `docs/ai/GREPLOOP.md`; `AGENTS.md` | Procedure: `gh pr comment … "@greptileai"` | N/A (process) | PR comments / reviews | **No** — not part of runtime pipeline | **No** | `.github/` has `codex_pr_review.yml` only; **no** `greptile` in `.github` (rg) | Phase 9: open PR, push, `@greptileai`, fix actionable, iterate (max 5) |

---

## WFC vs regular walk-forward — honesty

| Aspect | Regular walk-forward | Walk Forward Correlation |
|--------|----------------------|---------------------------|
| **Purpose** | Chronological B4 stages (Discovery → Confirmation → Holdout → Recent holdout); tune only where allowed | Full parameter-matrix IS/OOS correlation; surface shape persistence |
| **Implementation owner** | `campaign_runner.py` + `walk_forward.yaml` + `decision_engine/.../walk_forward.py` | `evaluate_wfc_gate` in `wfc/gate.py` + `wfc_gate.yaml` |
| **Ordering in workbench** | WFC runs **before** B4 period evaluation when bounds exist (`WALK_FORWARD_CAMPAIGNS.md`) | Same campaign — not a separate autoresearch gate |
| **Autoresearch treatment** | Fold metrics may appear in `summary.json` / merged metrics | `robustness_pass = (wfc_status == "PASS") or robustness_passed` (`generation_loop.py:150`) — **single boolean** |
| **Independence** | Assignment requires both gates PASS separately | **Not enforced** — no `regular_walk_forward_gate` / `walk_forward_correlation_gate` symbols in `packages apps tests` (rg exit 1) |
| **Double-WF** | `double_wf.py` / `evaluate_double_wf` targets WF1↔WF2 matrix correlation | Present in repo, wired in `packages/hft3/research/run_autonomous.py` stubs/tests — **not** in `campaign_runner` or `--autoresearch` |

**Conclusion:** Workbench implements both processes, but the **autoresearch active path does not distinguish or independently certify them**. Phase 1+ must add `run_generation_gate_chain` and separate receipts; reuse `evaluate_wfc_gate` — do **not** add a second WFC implementation.

---

## Autoresearch active path (observed)

```
run_pipeline.py --autoresearch
  → run_autoresearch_loop (generation_loop.py)
    → propose_next_candidates / generate_candidates
    → run_single_generation
        → freeze_candidate_manifest (no ontology gate)
        → filter_candidates / persist_screening_artifact (VectorBT; surface_stability in adapter)
        → _run_robustness_top_k → make_default_robustness_fn → run_campaign(allow_partial=True)
        → _run_hft_campaign (if run_hft_campaign; default false)
        → build_generation_summary (permissive elite)
        → .generation_complete (before full receipt validation)
```

**Permissive patterns (must remove in Phase 1+):**

- `generation_loop.py:135` — `allow_partial=True`
- `generation_loop.py:150` — `robustness_pass = wfc_status == "PASS" or robustness_passed`
- `generation_summary.py:141-142` — `robustness_pass is not False`; `hft_replay_status not in ("fail", "blocked")`
- `config/autoresearch/default.yaml:12-13` — `run_hft_campaign: false`, `hft_stages: [0]`

---

## Authority / transcript anchors

| Anchor | Location |
|--------|----------|
| Martyn Tinsley WFC | `docs/project/ROBUSTNESS_TESTING_SPEC.md:29` (transcript citation; no video URL in repo) |
| WFC campaign doc | `docs/workbench/WALK_FORWARD_CAMPAIGNS.md` § WFC gate |
| Greptile procedure | `docs/ai/GREPLOOP.md` § PR GrepLoop (`@greptileai`) |

---

## rg evidence tails (assignment §2 + §22)

### §2 — autoresearch entrypoints

```
scripts/run_pipeline.py:198:            run_autoresearch_loop,
scripts/run_pipeline.py:212:        code, report = run_autoresearch_loop(
packages/research_pipeline/generation_loop.py:323:def run_single_generation(
packages/research_pipeline/generation_loop.py:427:def run_autoresearch_loop(
packages/research_pipeline/elite_refinement.py:40:def propose_next_candidates(
```

### §2 — gate chain symbols (expected absent pre-Phase 1)

```
rg "regular_walk_forward_gate|walk_forward_correlation_gate|run_generation_gate_chain|FINAL_PASS" packages apps tests
→ exit 1 (no matches)
```

### §2 — Martyn Tinsley / WFC

```
docs/project/ROBUSTNESS_TESTING_SPEC.md:29: Walk Forward Correlation | User-provided transcript from "Martyn Tinsley - Walk Forward Correlation..."
apps/workbench/src/robustness/wfc/gate.py:1: """Walk Forward Correlation gate evaluation."""
```

### §2 — permissive autoresearch / elite

```
packages/research_pipeline/generation_loop.py:135:            allow_partial=True,
packages/research_pipeline/generation_loop.py:150:            robustness_pass = summary.get("wfc_status") == "PASS" or summary.get("robustness_passed") is True
packages/research_pipeline/generation_summary.py:141:            and row.get("robustness_pass") is not False
packages/research_pipeline/generation_summary.py:142:            and row.get("hft_replay_status") not in ("fail", "blocked")
config/autoresearch/default.yaml:12:run_hft_campaign: false
```

### §2 — Greptile (docs only; not in `.github`)

```
docs/ai/GREPLOOP.md:101:gh pr comment <PR_NUMBER> --body "@greptileai"
.github/workflows/codex_pr_review.yml  (Codex only — no greptile string)
```

### §22 — positive pearson/spearman (workbench + bridge; not autoresearch receipts)

```
apps/workbench/config/wfc_gate.yaml:13:pearson_min: 0.20
apps/workbench/src/robustness/wfc/gate.py:364:    pearson = _median_correlation(vectors, lambda a, b: stats.pearsonr(a, b)[0])
apps/workbench/src/robustness/wfc/gate.py:365:    spearman = _median_correlation(vectors, lambda a, b: stats.spearmanr(a, b)[0])
rg "parameter_universe_hash|aligned_parameter_hashes" → matches only in assignment/plan docs (not implementation)
```

---

## Blockers before Phase 1

1. **No `run_generation_gate_chain`** — strict receipt schema and exact PASS comparisons absent.
2. **No per-gate artifact paths** under `generation_<N>/gates/<candidate_id>/`.
3. **Ontology gate not before VectorBT** on autoresearch path.
4. **Regular WF and WFC not independent** — bundled in `run_campaign`; single `robustness_pass` boolean.
5. **`allow_partial=True`** in autoresearch robustness wrapper (certifying mode forbidden).
6. **Elite / best_candidate permissive** — not `FINAL_PASS`-gated; includes VectorBT-only passers.
7. **Statistical gauntlet not wired** — `robustness_bridge` / producers not invoked from `generation_loop`.
8. **HFT disabled by default** — `run_hft_campaign: false`; campaign-level status, not per-candidate gate.
9. **`.generation_complete` before receipt validation** — marker written in `run_single_generation` before gate validation.
10. **WFC receipt schema incomplete** vs assignment §10 (`parameter_universe_hash`, aligned hashes, scatter rows, quadrants).
11. **`evaluate_double_wf` not wired** to workbench campaign runner (optional additive path; document if required).
12. **Greptile** — procedure documented; no Phase 0 PR/integration proof (Phase 9 blocker by design).
13. **Config hash too narrow** — `compute_config_hash` omits WF/WFC/HFT/gate versions (`generation_loop.py:444-451`).

---

## Validation honesty (Phase 0)

```
merge-ready: no
scope-green: n/a (audit only, no code change to production gates)
scope: Phase 0 active-path audit per assignment §2
verify-run: audit rg + file reads (exit 0); no pytest gate claimed
data-mode: offline / docs + code inspection
known-gaps: full gate chain, Greptile PR loop, three-gen acceptance — all pending Phase 1+
```
