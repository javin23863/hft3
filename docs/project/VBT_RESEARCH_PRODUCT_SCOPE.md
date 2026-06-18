# VectorBT research product scope map (derivative handoff)

Status: derivative condensation, not canonical authority. This file exists only
to help humans and agents navigate the current paid JSONL/Vast thread. The
canonical product authority remains [OPPORTUNITY_RESEARCH_SPEC.md](OPPORTUNITY_RESEARCH_SPEC.md),
[VECTORBT_SCREENING_ENGINE_SPEC.md](VECTORBT_SCREENING_ENGINE_SPEC.md),
[MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md](../cockpit/MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md),
and [FEATURE_LITERATURE_TRACEABILITY_MATRIX.md](FEATURE_LITERATURE_TRACEABILITY_MATRIX.md).
If this file conflicts with those docs, those docs win. Update those docs first.
Read this before [VBT_PAID_SCREEN_UNIT_SCOPE.md](VBT_PAID_SCREEN_UNIT_SCOPE.md)
only as a handoff map.

Authority map: [OPPORTUNITY_RESEARCH_SPEC.md](OPPORTUNITY_RESEARCH_SPEC.md), [VECTORBT_SCREENING_ENGINE_SPEC.md](VECTORBT_SCREENING_ENGINE_SPEC.md), [MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md](../cockpit/MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md), [FEATURE_LITERATURE_TRACEABILITY_MATRIX.md](FEATURE_LITERATURE_TRACEABILITY_MATRIX.md) F004, [ECONOMIC_EVENT_UNIVERSE.md](../vault/ECONOMIC_EVENT_UNIVERSE.md), [HOT_MEMORY_UNIVERSE.md](../workbench/HOT_MEMORY_UNIVERSE.md).

2026-06-17 correction: do not treat the family labels below as a new ontology
or as a replacement for the three research clocks. They are a temporary
navigation aid. The artifact contract is `feature_plane_status` plus
point-in-time feature-usage proof per the canonical specs.

## What the owner is asking for (not optional nuance)

The research product is **not** “run each hypothesis on each scheduled event window and stop.”

At each **decision timestamp** (event window or continuous bucket), the system must evaluate:

| Data family | Role at decision time | Authority |
|-------------|----------------------|-----------|
| Target futures MBO | Primary `fs_v1` book/flow/queue/spread/regime slots | `specs/FEATURES.md`, `MarketStatePipeline` |
| Cross-asset futures | ES/NQ/ZN/ZB (and micros) **aligned** at the same instant | Hyp 16–20; vault `System Implications`; `build_event_cross_asset_snapshot.py` |
| VIX / VVIX sensors | Vol regime **at that instant** via `cross_asset_features["VIX"]` | `vix_features.py`, `vix_modules.py`; sensors not executable |
| VIX options | Vol-of-vol / quote state when PIT-valid | `C:\hft3-lake\vix_options`, checklist §VIX |
| CME options | Chain liquidity, skew, gamma/dealer proxies when PIT-valid | `specs/OPTIONS_LANE.md`, Lee/Ryu/Yang/Yu; O'Donovan/Yu/Zhang |
| Earlier macro prints | **Context** for a later target (ADP→NFP, PPI→CPI, Treasury→GDP, etc.) | Checklist §Owner Intent, §Macro Context |
| Continuous session state | Book toxicity, vacuum, spread stress, queue depletion **between** named events | OPPORTUNITY_RESEARCH_SPEC Continuous Intraday Unit |

Two measurements must stay **separate**:

1. **Standalone target** — Can the model make money trading CPI (or ADP) as the target event?
2. **Context uplift** — Does knowing NFP/Treasury/GDP/VIX/options **before** the target decision improve CPI/NFP trading vs a target-only baseline?

A profitable ADP standalone result is **not** proof that ADP improves CPI. That requires a Context-Feature Uplift Unit with ablation rows.

## Three research clocks (closed enum)

Code: `packages/backtest_pipeline/src/research_clock.py`

| `research_clock` | Question | Required unit type |
|------------------|----------|-------------------|
| `scheduled_event` | Trade a named release window (CPI, NFP, FOMC, EIA, GDP, …) | Event Target Unit |
| `context_feature_uplift` | Does PIT context (macro, VIX, options, cross-market) improve a **later** target? | Context-Feature Uplift Unit |
| `continuous_intraday` | Opportunity from book/flow/regime/cross-asset **through the session** | Continuous Intraday Unit |

Every screening artifact must declare `research_clock` (and opportunity metadata). Mixed runs must label each row’s clock — see [VECTORBT_SCREENING_ENGINE_SPEC.md](VECTORBT_SCREENING_ENGINE_SPEC.md).

### Event Target Unit fields

```text
model_id, target_event_type, symbol, latency_band_ms, event_window_id,
feature_set_id, target_only_result, context_result_or_not_measured, robustness_result_or_not_measured
```

### Context-Feature Uplift Unit fields

```text
model_id, target_event_type, context_event_type_or_feature_family,
allowed_context_window, symbol, latency_band_ms,
target_only_baseline, target_plus_context_result, delta_after_costs,
PIT_proof, context_ablation_result, robustness_result
```

### Continuous Intraday Unit fields

```text
model_id, opportunity_type, decision_timestamp_or_bucket, symbol,
latency_band_ms, feature_set_id, target_horizon, PIT_proof,
execution_assumption, robustness_result
```

`opportunity_type` includes: `continuous_intraday`, `liquidity_vacuum`, `spread_stress`, `queue_depletion`, `flow_toxicity`, `cross_asset_divergence`, `volatility_regime`, `options_liquidity_regime`, `pre_event_positioning`, `post_event_reaction`, … ([OPPORTUNITY_RESEARCH_SPEC.md](OPPORTUNITY_RESEARCH_SPEC.md) §Continuous Intraday Unit).

## Owner intent (macro / VIX / options) — checklist excerpts

From [MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md](../cockpit/MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md) §Owner Intent:

- Major volatility events as **primary targets**: CPI, CORE_CPI, NFP, claims, FOMC, GDP, PCE, PPI, …
- Smaller events may be **standalone targets** if they pass robustness on their own.
- Smaller events may also be **PIT context** for a later target (ADP, JOLTS, ECI, durable goods, ISM, EIA, construction spending, …).
- **Measure both separately** — profitable on ADP alone ≠ ADP improves NFP/CPI.
- VIX and VIX options state as context when PIT-valid.
- CME options state as context when PIT-valid.
- Required research unit granularity: `model + target_event + allowed_context_set + symbol + latency_band` (not model alone).

Ablation matrix required before context claims: target-only, target+macro, target+VIX, target+options, full context — with negative controls for leakage.

## Where the notes live (repo mirror of vault)

| Topic | Primary repo docs |
|-------|-------------------|
| Full opportunity taxonomy | [OPPORTUNITY_RESEARCH_SPEC.md](OPPORTUNITY_RESEARCH_SPEC.md) |
| Macro context / VIX / options | [MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md](../cockpit/MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md) |
| Context feature traceability | [FEATURE_LITERATURE_TRACEABILITY_MATRIX.md](FEATURE_LITERATURE_TRACEABILITY_MATRIX.md) F004 |
| Event catalog + cross-asset L3 snapshots | [ECONOMIC_EVENT_UNIVERSE.md](../vault/ECONOMIC_EVENT_UNIVERSE.md), `economic_event_universe/` |
| VIX sensors + HOT/WARM symbols | [HOT_MEMORY_UNIVERSE.md](../workbench/HOT_MEMORY_UNIVERSE.md) |
| Model + defensive composition | [MODEL_CATALOG.md](../workbench/MODEL_CATALOG.md) |
| VectorBT measurement contract | [VECTORBT_SCREENING_ENGINE_SPEC.md](VECTORBT_SCREENING_ENGINE_SPEC.md) |
| Options lane | `specs/OPTIONS_LANE.md` |
| Open PIT / coverage questions | [OPEN_QUESTIONS_AND_REJECTIONS.md](OPEN_QUESTIONS_AND_REJECTIONS.md) Q002, E001 |

Vault paths cited in specs (read on workstation): `wiki/hot.md`, `Home.md`, `library/System Implications.md`, `library/11 Options Microstructure.md`, `pipelines/Event Replay and Backtesting.md`, `architecture/Economic Event Universe.md`.

## What each engine actually feeds today

| Engine | Decision-time data | Context uplift | Continuous |
|--------|-------------------|----------------|------------|
| **Stage A** (`stage_a_screen.py`) | Full `fs_v1` row matrix `X` at latency-adjusted visible time; VIX injected into `cross_asset_features`; regime slots 41–49 | **Not measured** — cell is `(hyp_id, event_type)` only | **No** |
| **VectorBT paid JSONL** (`run_vectorbt_paid_screen.py`) | Bar OHLCV from NPZ → `close` fed into pipeline stub; **not** full MBO row loop | **No** ablation units | **No** |
| **Event replay** (`ReplaySession`, `sensor_feature_npz`) | Full MBO + optional sensor NPZ map into `cross_asset_features` | Historical path; not VectorBT discovery | Partial (event-driven steps) |
| **Workbench campaign** | Per-event diagnostics; options fixture contract marks context `not_measured` unless artifact proves uplift | Schema stubs in `campaign_runner.py`; producer contract fail-closed | Campaign mode exists; not same as paid JSONL |

### Implementation gaps (explicit — do not pretend done)

From [HOT_MEMORY_UNIVERSE.md](../workbench/HOT_MEMORY_UNIVERSE.md) phase table:

- Cross-asset features (PDF phase 4): **not implemented** in hot-memory layer
- Cross-asset graph: **not implemented**
- True multi-symbol MBO alignment for lead-lag hyps: **needs proof**; placeholder `own_ofi` rejected as evidence ([FEATURES.md](../../specs/FEATURES.md) slots 50–63 note)

From [OPPORTUNITY_RESEARCH_SPEC.md](OPPORTUNITY_RESEARCH_SPEC.md) §Data Findings:

- “Continuous tape research, true cross-asset feature usage, and context-feature uplift need their own artifact proof before broad claims.”
- Feature fabric: 47 PIT-eligible catalog rows, `model_feature_usage_status=not_observed` — catalog ≠ model consumption
- Stage A artifacts observed `sum_n_events_with_vix=0` on some runs — VIX hypotheses silent when coverage missing

From [MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md](../cockpit/MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md):

- Model Detail bar chart shows per-event EV, **not** context-for-target uplift
- Pass 4 (context feature generation) blocked until measurement schema is fail-closed

## Correct unit manifest shape for "full product" (derivative map, not ontology)

A complete discovery sweep is described canonically by the three research
clocks in [OPPORTUNITY_RESEARCH_SPEC.md](OPPORTUNITY_RESEARCH_SPEC.md). The
lettered families below are only a local explanation of that scope, not a new
source of truth:

```text
A) Event Target units
   ∀ survivor (slug, event_type) × M6 symbols × TIGHT event_ids

B) Context-Feature Uplift units
   ∀ allowed (target_event_type, context_event_type_or_family) × slug × symbol × latency_band
   from explicit context catalog (checklist §Macro Context — conservative mappings, not fuzzy LLM)

C) Continuous Intraday units
   ∀ slug × symbol × opportunity_type × decision_bucket × latency_band
   where opportunity_type ∈ continuous_intraday | spread_stress | flow_toxicity | ...

D) Options-context rows (when PIT-valid)
   target futures event + options feature family at decision timestamp
   separate from standalone FOPT strategy tests (F005)
```

**Current generator** (`generate_vbt_paid_units_jsonl.py`) implements **family A only** — scheduled-event target clock.

Renting Vast for family A alone is valid **only** if the run declaration says:

- `research_clock=scheduled_event`
- `context_result_or_not_measured` on every row
- `continuous_intraday` explicitly out of scope
- `model_feature_usage_status` honestly reflects bar-stub vs full fs_v1 path

Claiming "all features looked at" or "full backtest" from family A alone is
**false** per OPPORTUNITY_RESEARCH_SPEC. A run must instead declare
`feature_plane_status` and prove model consumption of admitted feature families,
or label itself `scheduled_event_only`, `bar_stub_research_only`, or
`incomplete_feature_plane`.

## Before backtesting: feature coverage checklist

For each planned unit, confirm at decision timestamp `t_dec`:

| Check | Pass criterion |
|-------|----------------|
| Primary MBO | Runnable NPZ / fs_v1 row exists for symbol at `t_dec` with latency shift |
| Cross-asset | Multi-symbol alignment artifact or `cross_asset_features` populated (not empty default) |
| VIX | `n_events_with_vix > 0` or unit sidelined with missing_scope |
| VIX options | Lake path + PIT join proof or `not_measured` |
| CME options | Context coverage artifact or dependency-scoped skip ([OPTIONS_LANE.md](../../specs/OPTIONS_LANE.md) defects) |
| Context macro | Earlier release timestamp `< t_dec`; context_ablation row planned |
| Continuous | `opportunity_type` and bucket defined; not conflated with TIGHT window |
| Filtration | `source_ts ≤ feature_available ≤ t_dec` ([checklist §Ontology Guardrails](../cockpit/MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md)) |
| Uplift claim | `target_only_baseline` run exists before `target_plus_context` |

## Recommended sequencing (ontology-aligned)

1. **Do not** rent Vast claiming full product until unit manifest declares clocks and families.
2. **Pilot** scheduled-event units with **full fs_v1 + sensor injection** path (parity with Stage A data plane), not bar-close stub — or label artifact `bar_stub_research_only`.
3. **Add** context-uplift unit generator + ablation artifact schema (checklist Pass 3–4).
4. **Add** continuous unit generator with `opportunity_type` labels.
5. **Prove** cross-asset alignment (L3 snapshot / multi-NPZ join) before cross-asset or options-context promotion.
6. VectorBT screen → robustness → HftBacktest realism on **promoted_ids per clock**, labeled separately in cockpit.

## Related docs

- Model composition (layers): [VBT_MODEL_ONTOLOGY.md](VBT_MODEL_ONTOLOGY.md)
- Hypothesis → slot reads: [VBT_HYPOTHESIS_FEATURE_MAP.md](VBT_HYPOTHESIS_FEATURE_MAP.md)
- Current paid JSONL only: [VBT_PAID_SCREEN_UNIT_SCOPE.md](VBT_PAID_SCREEN_UNIT_SCOPE.md)
- Rent phases: [VBT_PAID_SCREEN_RUNBOOK.md](VBT_PAID_SCREEN_RUNBOOK.md)
