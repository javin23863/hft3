# VectorBT paid screen model ontology

Status: historical / inactive for active HftBacktest-only pipeline routing.
Binding only for model identity and legacy paid-screen interpretation. Active
HBT runs follow [HFTBACKTEST_ONLY_PIPELINE_PLAN.md](HFTBACKTEST_ONLY_PIPELINE_PLAN.md)
and do not require Stage A survivor cells or VectorBT screening artifacts.

Authority chain (do not invent parallel processes):

| Layer | Document / code |
|-------|-----------------|
| Research clocks & unit types | [OPPORTUNITY_RESEARCH_SPEC.md](OPPORTUNITY_RESEARCH_SPEC.md) |
| Discovery entry order | [RESEARCH_ENTRYPOINTS.md](../vault/RESEARCH_ENTRYPOINTS.md) §1 |
| 64-slot feature set | [specs/FEATURES.md](../../specs/FEATURES.md), `feature_index.py` |
| Model identity (canonical registry slugs, including HYP/PDF/RL entries) | `packages/features_engine/config/model_registry.yaml` |
| Hypothesis signal logic | `packages/features_engine/src/hypotheses/modules.py`, `vix_modules.py` |
| VectorBT screen engine | [VECTORBT_SCREENING_ENGINE_SPEC.md](VECTORBT_SCREENING_ENGINE_SPEC.md), `vectorbt_adapter.py` |
| Paid rent phases | [VBT_PAID_SCREEN_RUNBOOK.md](VBT_PAID_SCREEN_RUNBOOK.md), [VBT_PAID_SCREEN_UNIT_SCOPE.md](VBT_PAID_SCREEN_UNIT_SCOPE.md) |
| Full product scope (three clocks) | [OPPORTUNITY_RESEARCH_SPEC.md](OPPORTUNITY_RESEARCH_SPEC.md); [VBT_RESEARCH_PRODUCT_SCOPE.md](VBT_RESEARCH_PRODUCT_SCOPE.md) is a derivative handoff map only |
| Macro / VIX / options context | [MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md](../cockpit/MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md) |
| Stage A (separate job) | `scripts/run_stage_a_screen.py` → `stage_a_survivors.json` |

## Critical scope warning

The current paid JSONL generator and `run_paid_screen.py` / `run_vectorbt_paid_screen_v2.py` implement
**scheduled-event target units only**. In the derivative
[VBT_RESEARCH_PRODUCT_SCOPE.md](VBT_RESEARCH_PRODUCT_SCOPE.md) map this is
called "family A," but the canonical classification is
`research_clock=scheduled_event` plus `feature_plane_status`.

They do **not** yet implement:

- Context-feature uplift (ADP→NFP, VIX/options at `t_dec`, target-only vs target+context ablation)
- Continuous intraday opportunity units
- Full `fs_v1` MBO row loop with VIX/options/cross-asset injection (VectorBT path uses bar OHLCV stub today; Stage A uses full feature matrix + VIX)

Do not describe Phase D Vast rent as “full backtest” or “all features” until the manifest declares research clocks and the data plane matches Stage A / replay injection paths.

## Historical Phase D Path (inactive)

```text
stage_a_survivors.json × events.csv TIGHT × CME M6 symbols
  → expand to Event Target units (model × symbol × event_id)
  → run_pipeline.py --vectorbt --vectorbt-scope paid-compute (per unit, on Vast)
  → screening_artifact.json (Screening Artifact Unit + per-candidate rows)
  → robustness gates (not on Vast rent job)
  → HftBacktest realism on promoted_ids only (not discovery)
```

In the historical VectorBT paid-screen lane, unit generation ran on the Vast
host. The former full default was `run_vbt_paid_screen_vast_full.sh`, which
consumed `research_cards/stage_a_full/stage_a_survivors.json` and applied the
runnable-NPZ filter before workers started. This is not active HBT-only routing.
`--all-active-models` / `VBT_UNIT_SOURCE=all_active` was an exploratory override
inside that inactive lane.

## What we are not doing

| Retired / wrong path | Why |
|---------------------|-----|
| `run_event_universe` as broad discovery | [OPPORTUNITY_RESEARCH_SPEC.md](OPPORTUNITY_RESEARCH_SPEC.md) — discovery is VectorBT/workbench screening first |
| `replay_matrix`, `run_event_replay` for new VectorBT work | [RESEARCH_ENTRYPOINTS.md](../vault/RESEARCH_ENTRYPOINTS.md) §1a retired |
| CPI+NFP × HYP_5 only as “full backtest” | One hypothesis × two event types; current full rent spans Stage-A survivor cells × CME M6 TIGHT event-symbol rows |
| M6 `run_event_universe` unit shape as 1:1 comparator | M6 runs all hypotheses inside one NPZ×latency replay unit; VectorBT paid units are **one hypothesis per event×symbol** |

## Research unit types (what to look for in artifacts)

From [OPPORTUNITY_RESEARCH_SPEC.md](OPPORTUNITY_RESEARCH_SPEC.md):

### Screening Artifact Unit (discovery handoff)

Required fields include: `screening_backend=vectorbt`, `feature_set_id`, `candidate_ids`, `promoted_ids`, `rejected_ids`, `no_lookahead_signal_shift_proof`, `events_csv_hash`, `lake_manifest_hash`, `research_clock`, `screening_artifact_hash`.

One paid-screen **work unit** produces one screening artifact (or a cached copy under `units/<unit_id>/`).

### Event Target Unit (what each JSONL row represents)

```text
model_id          — canonical slug from model_registry (not legacy HYP_N in production artifacts)
target_event_type — events.csv event_type column
symbol            — e.g. MES.v.0
latency_band_ms   — VectorBT screen uses bar clock; HBT realism adds measured latency later
event_window_id   — events.csv event_id (e.g. CPI_2024_09_11_TIGHT)
feature_set_id    — fs_v1 for production 64-dim vector
```

Paid JSONL row = one Event Target Unit **before** robustness/HBT columns are filled.

## What a “model” consists of (layers)

A tradable **hypothesis model** in this stack is not a single feature. It is the composition below.

### 1. Identity (`model_registry.yaml`)

| Field | Meaning |
|-------|---------|
| Canonical `model_id` | Slug, e.g. `SPREAD_BLOWOUT_RECOMPRESSION` |
| `legacy_id` | Deprecated `HYP_5` — resolve via `resolve_model_id()` |
| `hyp_id` | Integer 1–50 for hypotheses |
| `kind` | `hypothesis`, `pdf_structural`, or `reinforcement_learning` |
| `class` | Python class/artifact adapter name for the model family |

Inventory: the active identity source is every canonical descriptive slug in
`model_registry.yaml`, not the legacy `50 HYP + 11 PDF` phrase. That legacy
phrase records the hypothesis/PDF provenance subset and omits reinforcement
learning policy/proxy entries. `hyp_id` applies only to `kind: hypothesis`, while
RL entries carry explicit research-only or simulator-blocked status until their
own downstream validation and HBT order-adapter path exists.

### 2. Feature set (`feature_set_id = fs_v1`)

Production screening uses the **64-slot** vector defined in [specs/FEATURES.md](../../specs/FEATURES.md):

| Slot range | Family | Source |
|------------|--------|--------|
| 0–14 | Flow & book shape | MBO trades/book |
| 15–17, 27 | Spread regime | MBO book |
| 18–25 | Queue / toxicity proxies | MBO |
| 26 | Realized vol | MBO mid returns |
| 28–34 | Session / prop context | EventContextEngine + MBO |
| 40 | Mid price | BBO |
| 41–49 | Regime posterior `P(Z_t\|F_t)` | `RegimeFilter` |
| 50–63 | Structural model outputs | 11 PDF models via `StructuralModelIntegrator` |

**MarketState** (`X_t`) bundles:

- `primary_features` — dict view of slots 0–40
- `cross_asset_features` — per-symbol dicts (required for hyp 16–20)
- `regime_state`, `event_context`, `volatility_state`, `liquidity_state`
- `feature_vector` — optional 64-dim array for indexed `state.f()` access
- `regime_posterior` — slots 41–49 as dict

`MarketStatePipeline` + NPZ replay populate this on each bar/step. VectorBT paid path uses bar OHLCV driver + pipeline stub events (pilot/smoke plumbing); production Vast run must use validated lake NPZ hashes in the artifact.

### 3. Hypothesis signal (reads subset of fs_v1)

Each hypothesis class implements `evaluate(MarketState) -> float`. It reads **only the slots it needs** via `state.f('slot_name')`, plus optional gates on `event_context`, `regime_state`, `volatility_state`, or `cross_asset_features`.

**Every hypothesis uses fs_v1 infrastructure**, but signal logic touches a **sparse subset** of slots. Example: `SPREAD_BLOWOUT_RECOMPRESSION` (hyp 5) reads `spread_stress`, `book_slope` only.

Full hyp_id → slug → gates → slots table: [VBT_HYPOTHESIS_FEATURE_MAP.md](VBT_HYPOTHESIS_FEATURE_MAP.md).

### 4. Strategy parameters (VectorBT grid)

From `model_generation.py` with `expand_for_vectorbt=True`:

| Parameter | Role |
|-----------|------|
| `signal_threshold` | Entry when signal > threshold |
| `holding_period_bars` | Bar-count exit horizon |
| `stop_loss` / `take_profit` | From `DEFAULT_STRATEGY_PARAMS` grid |

Each `(model_id, param tuple)` is a **candidate_id** inside the screening artifact.

### 5. Screening gates (VectorBT only — not alpha proof)

`filter_candidates()` applies scope-specific gates (paid-compute requires Rust VectorBT + runtime proof). Promoted candidates carry `vectorbt_results`; they are **screen-passed**, not robustness-passed.

### 6. Defensive composition (workbench — not default paid JSONL)

Workbench supports `primary_model_id` + phased PDF stubs (`before`/`during`/`continuous`) per [MODEL_CATALOG.md](../workbench/MODEL_CATALOG.md). **Paid unit JSONL does not expand defensive stacks by default**; each row is one primary hypothesis slug unless explicitly extended in a future spec revision.

## Stage A vs VectorBT (different engines)

Stage A is a **separate historical job** (M6 cell filtering). Current Vast VectorBT full does not rerun Stage A; it consumes the completed survivor file as the survivor-scope input.

| | Stage A (historical / M6) | VectorBT paid screen (current) |
|---|---------|---------------------|
| Script | `run_stage_a_screen.py` | `run_pipeline.py` / `run_paid_screen.py` (v2) |
| Unit | `(hyp_id, event_type)` cell on feature store | `(slug, symbol, event_id)` |
| Output | `stage_a_survivors.json` (423 cells) | `screening_artifact.json` per unit |
| Engine | Cell expectancy on `fs_v1` features | Rust VectorBT bar simulation |
| Role | Survivor-cell filter for current Vast VBT scope | Cheap research prefilter; discovery handoff for HBT/robustness |

Stage A **423** = survivor **cells**, not paid work-unit count. Work units = `wc -l vbt_full_units.jsonl` after on-Vast expansion (`--from-stage-a-survivors` plus runnable-NPZ filtering).

## Paid work unit JSONL (canonical fields)

```json
{
  "unit_id": "SPREAD_BLOWOUT_RECOMPRESSION|MES.v.0|CPI_2024_09_11_TIGHT",
  "event_id": "CPI_2024_09_11_TIGHT",
  "symbol": "MES.v.0",
  "event_type": "CPI",
  "model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
  "hyp_id": 5,
  "thesis": "Spread blowout/recompression event-window strategy (SPREAD_BLOWOUT_RECOMPRESSION) on CPI release for MES.v.0 event CPI_2024_09_11_TIGHT"
}
```

`thesis` must resolve to the same slug via `hypothesis_parser` (`--no-llm` heuristic or legacy `HYP_N` resolution). **Do not** use bare `HYP_{n}` in thesis without slug/display keywords — heuristic parse would mis-assign models.

## Historical Checklist Before VectorBT Vast Rent (inactive)

The checklist below is retained only for interpreting old VectorBT receipts. It
does not authorize current HBT-only Vast execution or eligibility routing.

1. Units generated on Vast with `--from-stage-a-survivors research_cards/stage_a_full/stage_a_survivors.json` + full M6 symbol list + `events.csv` + runnable-NPZ filtering (or `run_vbt_paid_screen_vast_full.sh` default).
2. Every `model_id` is a registry **slug**; `hyp_id` matches `get_hyp_id_for_slug(model_id)`.
3. `paid_screen_ready_gate.json` → `ready_for_full_run: true` on **real** pilot/smoke hashes (not cloud fixture placeholders).
4. `expected_work_units` = JSONL line count from on-host generation (not 423 Stage A cells).
5. Workers ≥ 230 on 256 vCPU host.
6. Post-run: manifest terminal, `validate_screening_artifact` sample, `aggregate_vbt_promoted_ids.py` — no cockpit GREEN from partial JSONL.

Historical override only: `--all-active-models` + M6 symbol list
(active-registry expansion inside the inactive VectorBT lane).

## Related specs (downstream — not part of Vast VectorBT rent)

- [ROBUSTNESS_TESTING_SPEC.md](ROBUSTNESS_TESTING_SPEC.md) — DSR/PBO/CSCV after screen
- [HFTBACKTEST_REALISM_ENGINE_SPEC.md](HFTBACKTEST_REALISM_ENGINE_SPEC.md) — promoted_ids only
- [CME_M6_SWEEP_CONTROL_PLAN.md](../cockpit/CME_M6_SWEEP_CONTROL_PLAN.md) — execution realism gate (after screen)
