---
title: Continuous CME Microstructure Blueprint — Implementation Plan
status: planning
branch: plan/continuous-cme-microstructure-blueprint
created: 2026-06-26
source_authority: HFT3_Continuous_CME_Microstructure_Blueprint.pdf
---

# Continuous CME Microstructure Blueprint — Implementation Plan

## Source authority

| Field | Value |
|-------|-------|
| **Title** | HFT3 Continuous CME Microstructure Blueprint |
| **Subtitle** | All-encompassing developer implementation document — event-driven lane preservation + continuous CME-wide microstructure research expansion |
| **PDF path** | `C:\Users\MSI\Downloads\HFT3_Continuous_CME_Microstructure_Blueprint.pdf` |
| **Prepared** | 2026-06-26 UTC |
| **Classification** | Research and testing blueprint — **not** live-trading authorization |
| **Related repo docs** | `docs/research/AUTORESEARCH_PIPELINE.md` (to extend), future `docs/research/CONTINUOUS_CME_MICROSTRUCTURE.md` (implementation runbook) |
| **Planning standard** | [docs/project/PROJECT_PLANNING_STANDARD.md](../project/PROJECT_PLANNING_STANDARD.md) |

## Executive summary

The current hft3 pipeline is **event-driven**: it tests hypotheses around discrete macro, news, or scheduled trading events. That lane **remains intact**.

This blueprint adds a **second lane**: a **continuous CME-wide microstructure research lane** that:

1. Observes the entire tradable CME universe (not "trade every symbol").
2. Builds weekly Rithmic data-quality and coverage manifests (~40 GB/week).
3. Constructs a **relationship graph** between instruments (lead-lag, OFI, liquidity, spread, curve, seasonality, regime).
4. Generates **institutional microstructure hypotheses** (queue dynamics, OFI, toxicity, resiliency, hidden liquidity, term structure, cross-market impact, Hawkes/intensity).
5. Promotes only candidates that survive **cost-adjusted, execution-realistic, statistically deflated** testing (DSR/PBO, walk-forward, HftBacktest realism).

**Core philosophy:** observe every eligible CME symbol → rank relationships → generate hypotheses from measurable market structure → let gates decide what deserves research capital.

**Hard exclusions:** no retail strategies (opening-range breakout, MA crossover, RSI mean reversion, hard-coded commodity trades). No replacement of event lane, model families, data lake logic, or language boundaries.

**Language boundaries:** Python for research orchestration, registry, candidate generation, validation, manifests, graph scoring, RL artifacts. C++/Rust only at existing hot-path boundaries; no new compiled business logic unless repo interfaces require it.

---

## Two-lane architecture

```text
Event lane (preserve):
  thesis + event_id → parse hypothesis → event-window candidates
  → VectorBT / HftBacktest realism → statistical gates → promotion candidate

Continuous CME lane (additive):
  Rithmic weekly data → coverage manifest → data-quality + liquidity eligibility
  → feature store → CME relationship graph → continuous microstructure hypotheses
  → continuous candidates → cross-session / cross-symbol / cross-regime testing
  → statistical gates → promotion candidate
```

**CLI lane routing target:**

```bash
python scripts/run_pipeline.py --lane continuous --universe-profile full_cme_research \
  --rithmic-week 2026-W27 --build-relationship-graph --scan-continuous-candidates \
  --max-candidates 5000 --abort-on-failed-units false
```

---

## Mandatory review gate sequence

Every implementation batch on branch `plan/continuous-cme-microstructure-blueprint` (and downstream PRs) must pass gates in this order. **Do not skip or reorder.**

| # | Gate | Mandate | Blocks merge when |
|---|------|---------|-------------------|
| 1 | **Fable mindset** | Ground in real artifacts; name clock/metric/authority; verify with real checks. | Acting without evidence or conflating metrics (e.g. ack ms vs `tick_to_send_us`). |
| 2 | **Ponytail mindset** | YAGNI → stdlib → native → installed dep → minimum code. | Over-engineering, speculative abstractions, new deps without need. |
| 3 | **VaultGate + VaultPre** | `scripts/vault_gate.ps1 -Query "…"` + `scripts/vault_pre_edit.ps1`; consult vault `wiki/hot.md`, relevant `decisions/`. | Stale/missing vault stamp. |
| 4 | **GraphGate + GraphPre** | `scripts/graphify_gate.ps1` + `scripts/graphify_pre_edit.ps1` when graph **not** waived. | **Waived** per vault `wiki/hot.md` (`waived-by-owner-2026-06-16`) — skip until owner lifts waiver. |
| 5 | **Plan alignment** | Implementation must trace to checklist items in this document and PDF sections. | Work not mapped to a phase/checklist item. |
| 6 | **Locate — investigator** | `cavecrew-investigator` or `explore` for defs/callers/tests when paths unknown. | Blind edits without locating integration points. |
| 7 | **Edit — builder** | `cavecrew-builder` for surgical ≤2-file changes; main thread for approved multi-file batches. | Skipping delegation for non-trivial scope. |
| 8 | **Local preflight** | Task-specific `rg` loop per [docs/ai/GREPLOOP.md](../ai/GREPLOOP.md): forbidden legacy terms, missing authority rows, whitespace. | Preflight not run before reviewer. |
| 9 | **Dual-pass review** | `cavecrew-reviewer`: Pass A (Karpathy) + Pass B (BLUEPRINT/PDF math invariants — filtration, no lookahead, walk-forward). | Any 🔴 finding unresolved. |
| 10 | **Verify** | Bounded pytest/gates per [docs/VALIDATION_HONESTY.md](../VALIDATION_HONESTY.md); paste exit code + output tail. | `scope-green: no` or verify not run. |
| 11 | **GraphPost** | `graphify update .` or `scripts/graphify_rebuild.ps1` when graph tracked and not waived. | **Waived** until owner re-enables graph. |
| 12 | **Plan review agent** | **Second-to-last gate before PR.** Sole job: compare implementation vs PDF blueprint and this plan — verify **no drift**, **all PDF aspects covered or explicitly deferred**, phase acceptance gates met. Produce gap table (PDF section → code/doc/test → status). **Block PR if gaps remain undeferred.** | Any mandatory PDF requirement implemented differently, missing, or untested without documented deferral. |
| 13 | **PR prep loop (GrepLoop / Greptile)** | **Last gate before merge-ready PR.** Per [docs/ai/GREPLOOP.md](../ai/GREPLOOP.md) and Greptile skill: `@greptileai` review until ≥4/5 confidence, zero unresolved actionable findings on current head SHA. Local preflight + cavecrew-reviewer do **not** substitute. | Greptile unresolved or confidence below threshold. |

### Plan review agent — explicit mandate

The **plan review agent** is a dedicated pass (Task subagent or human owner) that runs **after Verify** and **before PR creation**:

1. Load PDF (`HFT3_Continuous_CME_Microstructure_Blueprint.pdf`) and this plan.
2. For each PDF section (1–16, appendices A–C), map to: files changed, tests, docs, config.
3. Flag **drift**: behavior that contradicts PDF hard constraints (§15).
4. Flag **gaps**: PDF-required files, model families, gates, or metrics not implemented.
5. Confirm Phase 0–8 acceptance gates (§13) for completed phases.
6. Output: `plan-review: pass | fail` with gap table; **`merge-ready: no`** until `pass`.

### PR prep loop — explicit mandate

Per [docs/ai/GREPLOOP.md](../ai/GREPLOOP.md):

- Greptile (`@greptileai`) is the **only** connector satisfying the PR GrepLoop gate.
- Run **after** plan review agent passes.
- Repeat fix → push → review until clean or bounded turn limit.
- Split PRs >~1000 lines or multi-subsystem where possible.

---

## Structured implementation checklist

Derived from PDF sections 1–16 and appendices. Check items as phases complete.

### Section 1 — Executive summary (scope anchor)

- [ ] Document two-lane architecture in `docs/research/CONTINUOUS_CME_MICROSTRUCTURE.md`
- [ ] Confirm event lane unchanged in integration tests
- [ ] Enforce promotion philosophy: no live eligibility from backtest alone

### Section 2 — Language boundaries

- [ ] Audit repo before edits; preserve Python research contract in YAML/artifacts
- [ ] No new C++/Rust business logic unless existing interface requires it
- [ ] Numba/NumPy acceleration only with deterministic Python interface + parity tests

### Section 3 — Phase 0: Bad NPZ / empty OHLCV remediation (immediate)

- [ ] Add `check_npz_ohlcv(path)` in `packages/research_pipeline/data_quality.py`
- [ ] Add `scripts/check_lake_data.py` → `valid_unit_ids` / `invalid_unit_ids` with reasons
- [ ] Update `config/autoresearch/default.yaml`: `skipped_unit_ids`, `abort_on_failed_units` by run type, `skip_bad_units_file`
- [ ] Update `scripts/run_pipeline.py`: load skip file, skip bad units, report skipped units
- [ ] Classify `NoOHLCVDataError` separately in `packages/research_pipeline/evaluation.py`
- [ ] Document in `docs/research/AUTORESEARCH_PIPELINE.md`
- [ ] Skip bad unit `ZN.v.0_EIA_NATGAS_2019_11_28_TIGHT`; resume run without re-running ~20,858 completed units
- [ ] Set `abort_on_failed_units=false` for `full_lake`, `paid`, `broad`, `continuous_full_cme` scopes
- [ ] **Acceptance:** run completes past bad NPZ; data-quality failures reported separately from model failures

### Section 4 — Target architecture: event + continuous lanes

- [ ] Implement `--lane continuous` routing in `scripts/run_pipeline.py`
- [ ] Shared promotion philosophy across lanes
- [ ] No rewrite of event-lane behavior except routing and shared reporting

### Section 5 — Data architecture (40 GB/week Rithmic)

- [ ] `packages/research_pipeline/continuous_data_manifest.py` — weekly coverage manifest
- [ ] Schema: `runtime/continuous_cme/coverage_manifest_YYYY_WW.json` (roots, contracts, data types, per-contract rows, missing_ratio, liquidity_score, eligible)
- [ ] `packages/research_pipeline/continuous_universe.py` — universe profiles, active contracts, coverage/liquidity filters
- [ ] `packages/research_pipeline/continuous_feature_store.py` — feature matrices from weekly data
- [ ] `packages/features_engine/config/cme_universe.yaml` — root metadata (asset class, tick size, session template, micro/standard)
- [ ] **Acceptance:** manifest reports roots, contracts, rows, missingness, eligibility

### Section 6 — CME universe and relationship graph

- [ ] `packages/research_pipeline/relationship_graph.py` — build and score graph
- [ ] `packages/features_engine/config/cme_relationship_graph.yaml` — micro_standard, metals_complex, energy_complex, rates_curve
- [ ] Output: `runtime/continuous_cme/relationship_graph/` weekly files
- [ ] Edge features: lagged correlation, lagged OFI beta, lead-lag stability, spread z-score, cointegration/residual, volume leadership, queue pressure divergence, impact decay half-life, cost feasibility
- [ ] **Acceptance:** edge scores for micro/standard, metals, energy, rates, calendar families

### Section 7 — Institutional continuous model families

Add to `packages/features_engine/config/model_registry.yaml` (continuous_eligible, not event_eligible):

| Model ID | Family |
|----------|--------|
| `MICRO_STANDARD_FLOW_TRANSFER` | Cross-market lead-lag (MES/MNQ/MGC/MCL vs ES/NQ/GC/CL) |
| `CROSS_MARKET_OFI_IMPACT` | Cross-asset flow (GC→SI, CL→RB/HO, ZN→ZB, ES→NQ) |
| `BOOK_RESILIENCY_CONTINUATION` | Liquidity resiliency |
| `QUEUE_DEPLETION_REPLENISHMENT` | Queue dynamics |
| `HIDDEN_LIQUIDITY_RELOAD` | Hidden liquidity / iceberg |
| `TOXIC_FLOW_ADVERSE_SELECTION` | Order-flow toxicity |
| `CALENDAR_CURVE_MICRO_IMPULSE` | Term structure |
| `STRUCTURAL_SPREAD_MICRO_DISLOCATION` | Relative value spreads |
| `SEASONAL_STATE_CONDITIONED_MICRO_ALPHA` | Seasonality conditioning |
| `SELF_EXCITING_FLOW_BURST` | Hawkes-like point process |
| `RL_EXECUTION_OVERLAY` | Execution overlay (not alpha) |

- [ ] Registry metadata: `kind: continuous_microstructure`, `requires_relationship_graph`, `required_data`, `valid_relationship_types`, `default_param_ranges`, `risk_metrics` (DSR primary, PBO guardrails)
- [ ] **Acceptance:** parser returns continuous lane profile and model family

### Section 8 — Feature taxonomy (point-in-time)

- [ ] Feature groups: order_flow, queue_dynamics, spread_depth, cross_market, calendar_curve, seasonal_state, toxicity, execution_cost
- [ ] Formulas: book imbalance, queue imbalance, OFI, micro-price, VWAP-to-mid, VAMP, lagged OFI beta, impact decay half-life
- [ ] **No lookahead:** no future bars, fills, post-event labels, or later-session aggregates at decision time
- [ ] **Acceptance:** timestamp leakage tests and missingness checks pass

### Section 9 — Candidate generation and registry integration

- [ ] `packages/research_pipeline/continuous_model_generation.py`
- [ ] `packages/research_pipeline/cross_market_features.py`
- [ ] `packages/research_pipeline/commodity_structure.py`
- [ ] `packages/research_pipeline/seasonal_state.py`
- [ ] Modify `packages/research_pipeline/hypothesis_parser.py` — parse `lane="continuous_microstructure"`, universe profile, relationship family; **no full universe expansion in parser**
- [ ] Candidate artifacts include: `relationship_id`, feature family, session scope
- [ ] **Acceptance:** candidates generated from graph edges and registry ranges only

### Section 10 — Continuous evaluation and alpha validation

- [ ] `packages/research_pipeline/continuous_evaluation.py`
- [ ] `packages/research_pipeline/statistics.py` — PSR, DSR, MinTRL
- [ ] `packages/research_pipeline/cross_validation.py` — CSCV/PBO
- [ ] `packages/research_pipeline/power_analysis.py`
- [ ] `packages/research_pipeline/cost_model.py` — spread, fees, slippage, impact
- [ ] Modify `packages/research_pipeline/evaluation.py` — route continuous candidates; statistical gates
- [ ] Metrics: gross/net PnL, fill-adjusted PnL, Sharpe, Sortino, DSR, PSR, PBO, CVaR, tail ratio, session/regime breakdowns
- [ ] Trial tracking for DSR (candidate counts, search budget)
- [ ] **Acceptance:** results include net PnL, cost breakdown, DSR/PBO, session/regime reports

### Section 11 — RL as execution overlay

- [ ] Modify `packages/research_pipeline/rl_agents.py` — continuous session episodes, execution overlay mode
- [ ] RL improves cost-adjusted results vs baseline; **does not fail base model if RL alone fails**
- [ ] Actions: wait, passive bid/ask, cross, cancel/replace, reduce size, flatten/avoid
- [ ] Artifacts: policy file, features, split, seed, reward definition, validation metrics
- [ ] **Acceptance:** RL compared to baseline; base model not failed solely by RL

### Section 12 — Implementation matrix (file inventory)

**Add:**

- `packages/research_pipeline/continuous_universe.py`
- `packages/research_pipeline/continuous_data_manifest.py`
- `packages/research_pipeline/continuous_feature_store.py`
- `packages/research_pipeline/relationship_graph.py`
- `packages/research_pipeline/cross_market_features.py`
- `packages/research_pipeline/commodity_structure.py`
- `packages/research_pipeline/seasonal_state.py`
- `packages/research_pipeline/continuous_model_generation.py`
- `packages/research_pipeline/continuous_evaluation.py`
- `packages/research_pipeline/continuous_ml_training.py`
- `packages/research_pipeline/data_quality.py`
- `packages/research_pipeline/statistics.py`
- `packages/research_pipeline/cross_validation.py`
- `packages/research_pipeline/power_analysis.py`
- `packages/research_pipeline/cost_model.py`

**Modify:**

- `packages/features_engine/config/model_registry.yaml`
- `packages/research_pipeline/hypothesis_parser.py`
- `scripts/run_pipeline.py`
- `packages/research_pipeline/evaluation.py`
- `packages/research_pipeline/rl_agents.py`
- `config/autoresearch/default.yaml`
- `docs/research/AUTORESEARCH_PIPELINE.md`
- `docs/research/CONTINUOUS_CME_MICROSTRUCTURE.md` (new runbook)

### Section 13 — Phased roadmap and acceptance gates

| Phase | Name | Acceptance gate |
|-------|------|-----------------|
| 0 | Data-quality recovery | Run completes past bad NPZ; failures classified |
| 1 | Continuous manifest | Manifest reports roots, contracts, rows, missingness, eligibility |
| 2 | Universe and graph | Edge scores for configured relationship families |
| 3 | Feature store | PIT features pass leakage + missingness tests |
| 4 | Model registry | Parser returns continuous lane profile/family |
| 5 | Candidate generation | Artifacts include relationship_id, feature family, session scope |
| 6 | Continuous evaluation | Net PnL, costs, DSR/PBO, session/regime reports |
| 7 | RL overlay | RL vs baseline; base not failed solely by RL |
| 8 | Dashboard/reporting | Summaries rank edges, candidates, passes/fails; no live eligibility by default |

### Section 14 — Operational runbook (post event run)

- [ ] Finish/resume current event run with skip-bad-units policy
- [ ] Build first continuous Rithmic manifest (one week, 40 GB)
- [ ] Relationship graph for top liquidity-eligible products first (not all low-liquidity contracts)
- [ ] Pilot families: micro-standard, metals GC/SI/MGC, energy CL/MCL/RB/HO/NG, rates ZT/ZF/ZN/ZB, calendar front/second
- [ ] Generate candidates only from stable graph edges
- [ ] Full gates before model library promotion

### Section 15 — Hard constraints (non-negotiable)

1. Do not replace event-driven pipeline
2. Do not add retail strategies as core continuous models
3. Do not treat all CME symbols as live-tradeable
4. Do not evaluate without contract metadata
5. Do not evaluate without data-quality pass
6. Do not run cross-market models without timestamp synchronization
7. Do not promote without cost-adjusted and execution-realistic testing
8. Do not let RL replace alpha models
9. Do not assume correlations stable — measure by week, session, product, regime
10. Do not hard-code gold/silver/oil/gas relationships as alpha
11. Do not create new C++/Rust surfaces unless existing interfaces require
12. Do not allow data-quality failure to masquerade as model failure

### Section 16 — References (literature receipts)

PDF cites: QuantStart microstructure intro, Bieganowski & Slepaczuk arXiv:2602.00776, OFI (Emergent Mind), Nevmyvaka RL execution (ICML 2006), Bailey PBO/CSCV, Bailey & LdP DSR, Harvey & Liu multiple testing, ADIA Sharpe ratio summary, Concretum data quality and intraday automation. Map to vault `library/` and `docs/references/` during implementation.

### Appendix A — Developer checklist

- [ ] Run existing tests before continuous-lane changes
- [ ] Phase 0 before broad relaunch
- [ ] Additive code under `packages/research_pipeline/` and config YAMLs
- [ ] Every new feature: unit tests + at least one integration smoke
- [ ] Every candidate artifact: lane, data coverage, relationship_id, feature family, cost model version, gate status
- [ ] Every result reports pass/fail reason — no silent filters

### Appendix B — Minimal pilot scope

| Pilot | Family | Products | Goal |
|-------|--------|----------|------|
| 1 | Micro-standard | MES/ES, MNQ/NQ, MGC/GC, MCL/CL | Pressure transfer, execution feasibility |
| 2 | Metals | GC/SI/HG + MGC/GC | OFI dislocation, spread residuals |
| 3 | Energy | CL/MCL/RB/HO/NG | Complex flow, seasonal conditioning |
| 4 | Rates | ZT/ZF/ZN/ZB/UB | Curve impulse, belly/long-end lead-lag |
| 5 | Calendar | Front vs second in CL, NG, GC, ZN | Roll-phase, maturity-liquidity shift |

First pilot: one Rithmic week, top data-quality + liquidity products only.

### Appendix C — Final developer instruction block

Implement continuous CME microstructure as **additive lane**. Preserve event lane. Python-first research. No retail strategies. Weekly manifests → data-quality → relationship graph → candidate generation → continuous evaluation → statistical gates → cost model → RL overlays. RL is execution overlay only. No promotion without data-quality, liquidity, cost, DSR/PBO, walk-forward, and execution-realism evidence.

---

## Merge strategy

| Item | Value |
|------|-------|
| **Worktree path** | `C:\Users\MSI\Documents\hft3-continuous-cme-blueprint` |
| **Branch** | `plan/continuous-cme-microstructure-blueprint` |
| **Base** | `main` at worktree creation |
| **Merge target** | `main` via PR after all phases complete and gates pass |
| **Recommended PR sequence** | Phase 0 (data quality) → Phase 1–2 (manifest + graph) → Phase 3–5 (features + registry + candidates) → Phase 6–8 (evaluation + RL + reporting) — split per [GREPLOOP.md](../ai/GREPLOOP.md) size guidance |
| **This commit** | Planning document only — no implementation |

```powershell
# After implementation PRs merge:
git checkout main
git merge plan/continuous-cme-microstructure-blueprint
git worktree remove C:\Users\MSI\Documents\hft3-continuous-cme-blueprint
```

---

## Known gaps and open items

| Gap | Source | Status |
|-----|--------|--------|
| Entire continuous lane | PDF §4–14 | **Not implemented** — plan only |
| Phase 0 bad NPZ remediation | PDF §3 | **Blocked current run** — `ZN.v.0_EIA_NATGAS_2019_11_28_TIGHT` empty OHLCV |
| `packages/research_pipeline/*` continuous modules | PDF §12 | **Do not exist yet** — verify paths against live repo before Phase 1 |
| `docs/research/CONTINUOUS_CME_MICROSTRUCTURE.md` runbook | PDF §12 | **Not written** — create during Phase 1 |
| Rithmic weekly 40 GB ingest path | PDF §5 | **Operational dependency** — CHI404/trial lane quarantine rules apply |
| Live eligibility | PDF §1, §14 | **Explicitly zero** until risk layer approves |
| Graph gates | vault `wiki/hot.md` | **Waived** `waived-by-owner-2026-06-16` — re-enable when owner lifts |
| Plan review agent | This document | **Not yet run** — runs per implementation batch before PR |
| Greptile PR GrepLoop | GREPLOOP.md | **Not yet run** — runs last before merge-ready PR |

---

## Validation honesty (this handoff)

```
merge-ready:     no
scope-green:     not-run
scope:           docs/plans/CONTINUOUS_CME_MICROSTRUCTURE_BLUEPRINT.md (planning only)
verify-run:      not-run (docs-only plan commit; no code changed)
data-mode:       n/a
known-gaps:      entire continuous lane unimplemented; Phase 0 NPZ blocker; plan review and Greptile gates pending implementation
```
