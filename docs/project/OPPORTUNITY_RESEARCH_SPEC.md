# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent project requirements outside that authority.

# Opportunity Research Spec

Status: historical planning-control correction after the 2026-06-16 CME M6 Vast
sweep; active pipeline routing is superseded by
[HFTBACKTEST_ONLY_PIPELINE_PLAN.md](HFTBACKTEST_ONLY_PIPELINE_PLAN.md). This
document still defines research questions and feature-scope lessons. Treat
VectorBT-first or Stage-A-first routing references below as historical unless an
owner explicitly re-enables the legacy path.

2026-06-16 M6/Vast lesson: broad `run_event_universe` was the wrong first
discovery path. Discovery must start with a first-class VectorBT/workbench
screening artifact. Execution-realism work consumes screened candidates through
the official-HftBacktest-backed realism runner; retired hft3 replay entrypoints
such as `replay_matrix` and `run_event_universe` are not valid fallback gates for
this implementation.

## 2026-06-17 Authority Correction

Do not create a second "full research product" scope document when this spec,
`docs/cockpit/MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md`,
`docs/project/VECTORBT_SCREENING_ENGINE_SPEC.md`, and
`docs/project/FEATURE_LITERATURE_TRACEABILITY_MATRIX.md` already define the
product. If derivative files such as `VBT_RESEARCH_PRODUCT_SCOPE.md`,
`VBT_MODEL_ONTOLOGY.md`, or `VBT_HYPOTHESIS_FEATURE_MAP.md` appear in another
branch or agent workspace, treat them as non-canonical condensations unless
they explicitly defer to these authorities. The canonical fix is to update the
existing authority docs, not to invent a parallel ontology.

The full research product is not an agent-defined `A union B union C union D`
manifest. The canonical product is the three-clock research system below, with
dependency-scoped feature admission and artifact proof. The required unit grain
is:

```text
model_id
research_clock
target_event_type_or_opportunity_type
allowed_context_set_id
symbol
latency_band_ms
feature_plane_status
```

At each decision timestamp, a model must either consume or explicitly sideline
each admitted point-in-time feature family: primary futures MBO / `fs_v1`,
cross-asset futures state, VIX/VVIX sensors, VIX options, CME options, earlier
macro releases, continuous/session book state, and latency state. Feature
existence in the lake is not feature usage. Every artifact must say which
families were used, which were absent, which were intentionally excluded, and
why.

The owner intent is two separate measurements:

- Smaller events may be standalone tradable targets if they pass robustness.
- Smaller events may also be features for later major volatility targets such
  as CPI, NFP, unemployment claims, FOMC, GDP, PCE, or PPI.

Those are not interchangeable. ADP profitability as a target does not prove ADP
improves NFP trading. A context claim requires target-only baseline,
target-plus-context result, delta after costs, PIT proof, and robustness state.

A VectorBT/Vast run that uses bar-only inputs, event-only JSONL, or an
incomplete feature plane is still useful as a scheduled-event screening slice,
but it must be labeled `bar_stub_research_only`, `scheduled_event_only`, or the
equivalent terminal artifact status. It must not claim full context-feature,
cross-asset, options, latency, or continuous-intraday coverage.

## Why This Exists

The CME M6 sweep is a valid event-window execution-realism gate for selected
candidates, but it is not the whole research product and it is not the first
discovery engine. The word "universe" in the original M6 plan meant all
selected symbols and scheduled event windows for the Stage A survivors. It did
not mean all opportunity types, all continuous intraday decision times, or all
context-feature uplift tests.

Future expensive runs must therefore declare the research clock and product
question before launch:

| Research clock | Question answered | Current M6 coverage |
|---|---|---|
| Scheduled-event target clock | Can a model trade a named event window, such as CPI, NFP, FOMC, EIA, or unemployment claims? | Covered only for candidates promoted by the VectorBT/workbench screening artifact into the M6 event-window gate. |
| Context-feature uplift clock | Does earlier PIT information, such as ADP, JOLTS, VIX, or options state, improve trading of a later target event beyond a target-only baseline? | Not covered by M6 unless explicit context-ablation artifacts exist. |
| Continuous intraday opportunity clock | Can a model find opportunity throughout the session from book state, flow toxicity, liquidity, regime, cross-asset state, options/volatility state, or session structure? | Not covered by the event-window M6 sweep. |

The system may still run event-window sweeps, but those artifacts must be
labeled as event-window evidence only.

## Source Authority Map

| Area | Local authority | Required consequence |
|---|---|---|
| Ontology gate | Vault `wiki/hot.md`, vault `Home.md`, repo `AGENTS.md` | Agents must check the vault before acting and must use the canonical repo. |
| Event catalog | Vault `pipelines/Event Replay and Backtesting.md`; `specs/PIPELINE.md`; `packages/data_system/config/events.csv` | Reuse the existing event universe/catalog. Do not invent a second event catalog; execution realism is handled by the official-HftBacktest-backed runner. |
| First-pass screening | Vault `pipelines/Event Replay and Backtesting.md`; `packages/backtest_pipeline/src/vectorbt_adapter.py`; workbench artifacts | Discovery uses a first-class VectorBT/workbench screening artifact before expensive replay. |
| Continuous decision loop | `specs/CHI404_RUNTIME.md`; `specs/HOT_PATH.md`; `docs/workbench/MODEL_CATALOG.md` | Continuous models are evaluated on book-changing/event-driven steps, not only named macro windows. |
| Feature slots and regime | `specs/FEATURES.md`; vault `library/03 MBO Event-Level Dynamics.md`; vault `library/05 Hawkes and Point Processes.md` | Book, queue, flow, structural, and regime features must be timestamped and PIT-safe. |
| OFI/MLOFI and impact | Vault `library/04 Order Flow Imbalance and Price Impact.md`; vault `library/papers/xu-gould-howison-2019-mlofi.md` | Multi-level OFI-style features need out-of-sample validation and tick-regime stability checks. |
| Flow toxicity / VPIN | Vault `library/01 Classical Market Microstructure.md`; `specs/FEATURES.md` slots 52-53 | Toxic-flow features are candidate continuous/session features, not proof of tradable edge without costs. |
| Options and VIX context | `docs/cockpit/MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md`; vault `library/11 Options Microstructure.md`; vault paper notes for O'Donovan/Yu/Zhang and Lee/Ryu/Yang/Yu | Options and VIX are features/sensors unless the options lane is testing standalone options strategies. Coverage, PIT proof, units, and ablation are required. |
| Robustness | Vault `library/13 Robust Backtesting and Multiple Testing.md`; vault `library/papers/dsr-pbo-bailey-lopezdeprado-source-map.md`; `docs/project/ROBUSTNESS_TESTING_SPEC.md` | Promotion requires DSR/PBO/CSCV/bootstrap/walk-forward/multiple-testing gates. |
| Cost and expensive-run control | `docs/cockpit/BUILDOUT_CORRECTNESS_CHECKLIST.md`; `specs/PIPELINE.md`; `docs/cockpit/CME_M6_SWEEP_CONTROL_PLAN.md` | Paid or rented replay compute is blocked until a VectorBT screening pilot artifact exists. Paid runs also need dry-run scope, time/cost estimate, checkpoint plan, worker-utilization rule, stall rule, and abort rule. |

## Required Research Units

Every research artifact must identify one of these units.

### Screening Artifact Unit

```text
screening_backend=vectorbt
vectorbt_version
vectorbt_engine=rust|numba|auto
screening_artifact_hash
workbench_run_id
feature_set_id
candidate_ids
candidate_reasons
promoted_ids
promoted_reasons
rejected_ids
rejected_reasons
no_lookahead_signal_shift_proof
license_review
events_csv_hash
lake_manifest_hash
created_at_utc
```

Use this before any M6 replay, rented compute, or all-scope robustness run.
The artifact is the discovery handoff. The official-HftBacktest-backed realism
runner consumes its promoted IDs for execution realism, robustness, and
failure-mode analysis. Retired hft3 replay entrypoints must not consume this
handoff as substitutes.

### Event Target Unit

```text
model_id
target_event_type
symbol
latency_band_ms
event_window_id
feature_set_id
target_only_result
context_result_or_not_measured
robustness_result_or_not_measured
```

Use this when the model trades CPI, NFP, FOMC, EIA, construction spending, or
any other scheduled event as the target.

### Context-Feature Uplift Unit

```text
model_id
target_event_type
context_event_type_or_feature_family
allowed_context_window
symbol
latency_band_ms
target_only_baseline
target_plus_context_result
delta_after_costs
PIT_proof
context_ablation_result
robustness_result
```

Use this when ADP, JOLTS, ECI, durable goods, ISM, VIX, VIX options, CME
options, cross-market state, or any smaller event is used as a feature for a
later target event.

### Continuous Intraday Unit

```text
model_id
opportunity_type
decision_timestamp_or_bucket
symbol
latency_band_ms
feature_set_id
target_horizon
PIT_proof
execution_assumption
robustness_result
```

Required `opportunity_type` values start as:

```text
scheduled_event_window
continuous_intraday
session_open
session_close
prop_flatten_window
liquidity_vacuum
spread_stress
queue_depletion
flow_toxicity
cross_asset_divergence
volatility_regime
options_liquidity_regime
energy_inventory_shock
post_event_reaction
pre_event_positioning
```

The list can grow only through the feature admission gate.

## Available-Data Operating Mode

The project does not wait for perfect historical coverage before continuing
research. The accepted Q001 rule is:

```text
Use available data.
Do not pretend missing data exists.
Sideline only the models, features, symbols, dates, or opportunity units that
declare a dependency on unavailable data.
Keep all unrelated available-data research moving with explicit coverage,
skip, or rejection reasons.
```

This rule applies to CME futures, CME options, volatility features, macro
features, and continuous intraday opportunity research.

Required artifact fields for dependency-scoped missing data:

```text
required_data_family
required_source_ids
coverage_status
missing_scope
model_treatment
skip_or_rejection_reason
can_run_on_available_data
```

Blocking is dependency-scoped, not global:

| Missing or partial data | Blocks | Does not block |
|---|---|---|
| Missing CME futures MBO for a specific event-symbol slot | That event-symbol unit and models requiring it. | Other symbols, other dates, other event windows, and continuous units with valid data. |
| Options strict quote-level MBO gaps | Strict quote reconstruction, strict quote-only options features, options order-book replay, and options model promotion that requires those quotes. | CME futures models that do not use those options features; options studies allowed by study/trade/NPZ coverage. |
| VIX/options-derived feature file missingness | Models or context-ablation rows that require the missing feature. | Target-only baselines and models whose declared feature set excludes that feature. |
| Slow-tier LLM label failure | Slow-tier narrative/label features. | Numeric market-data replay, Stage A/B research, and robustness paths that do not consume slow-tier labels. |

## Data Findings From Hot/Warm State

Observed on 2026-06-16 from local artifacts:

| Evidence | Observation | Research consequence |
|---|---|---|
| Active CME NPZ lake | `HFT3_NPZ_ROOT=C:\hft3-lake\npz`; current catalog shows 60,783 runnable records, 3,161 quarantine entries, 0 unaccounted, and on-disk NPZ inventory around 63,944 files. | The CME lake is usable for available-data research. Quarantine and missing-scope rows must remain explicit skips/rejections. |
| Raw MBO source layer | `C:\hft3-lake\mbo_release` contains event directories, raw DBN ZST files, and validation sidecars. | Raw presence is not the same as runnable feature/backtest evidence; conversion and manifest proof remain required. |
| Feature store | `C:\hft3-lake\features` contains `fs_v1` records for the current event-window feature path on ES/MES/MNQ/NQ/RTY/ZN/ZB. | These symbols can continue through available-data event-window research. Other HOT/WARM products need feature-build evidence before being treated as runnable. |
| Sensors and options side data | `C:\hft3-lake\sensors`, `C:\hft3-lake\options`, and VIX/options directories exist. | Treat as candidate feature sources until feature manifests prove PIT usage and model artifacts prove consumption. |
| `research_cards/stage_a_full/stage_a_result.json` | `run_id=all_lanes_20260614T045502Z`, `band_ms=6.255764`, 1,650 cells, 16,931 units run, 0 skipped, 0 errored, 423 Stage A survivors, 212 Holm survivors. | Stage A produced event-window candidates, not final robust models. Those candidates still require a first-class VectorBT/workbench screening artifact before expensive M6 replay. |
| Stage A positive cells | 28 cells had positive mean expectancy before full robustness. Examples included second-wave continuation/passive-trap-fill on construction spending, NFP, CPI, ECI, and core PPI. | Treat as hypotheses for robust replay, not proof. Tail losses and stale certification remain blockers. |
| `runtime/workbench/.../feature_fabric/...` | 47 PIT-eligible catalog rows, 0 rejected, `model_feature_usage_status=not_observed`. | The hot/warm feature fabric proves catalog eligibility, not model consumption. Models must emit feature-usage artifacts. |
| `runtime/data_doctor_report.json` | Lake catalog healthy: 60,783 records, 3,161 quarantine entries, 0 unaccounted. Options datasets present. | Available CME data is enough to continue available-data model work; missing options coverage must sideline only dependent models. |
| Current options data doctor | Options definitions 2,645 files, statistics 206 files, OHLCV one file, fixing MBO quote/trade coverage present. Current runtime report has `options-fixing-coverage` FAIL for recent dates `2026-06-15` and `2026-06-16`; strict MBO quote diagnostic has 509 gaps and 505 stale gaps. | This adds dependency-scoped options backfill work. It does not globally block CME futures or any model whose declared data requirements exclude those missing options rows. |
| `runtime/slow_tier/nightly_2026-06-15.log` | No digest trades; LLM label/brief failed from missing Ollama model and write permission. | Slow-tier labels are not valid model evidence until the local model and artifact write path are fixed. |
| Vast M6 monitor | Remote had 28,115 JSONL rows of expected 28,136 and no `universe_result.json` at the latest check; local cockpit artifact had only 333 rows. | Do not claim GREEN from partial row files. Add stall/tail-unit monitoring and import validation before cockpit aggregation. |
| Existing research path | Current implemented production-style research path is event-window Databento MBO NPZ -> `fs_v1` features -> workbench/backtest artifacts. | Continuous tape research, true cross-asset feature usage, and context-feature uplift need their own artifact proof before broad claims. |

## Lessons From The Vast M6 Sweep

These are binding planning lessons for future expensive runs:

- [ ] Broad M6 `run_event_universe` was the wrong path for discovery; it is a
  retired path, not a selected execution-realism or robustness gate for this
  VectorBT/HftBacktest implementation.
- [ ] M6 must consume a first-class screening artifact with `screening_backend=vectorbt`,
  `vectorbt_version`, `vectorbt_engine=rust|numba|auto`,
  `screening_artifact_hash`, candidate/promoted/rejected IDs and reasons,
  `no_lookahead_signal_shift_proof`, and `license_review`.
- [ ] No paid or rented replay compute may start until a VectorBT screening
  pilot artifact exists and validates.
- [ ] Scope name must be literal. "M6 event-window universe" is not "whole opportunity universe."
- [ ] Before renting compute, run a pilot that proves artifact schema, product question, row-count expectations, cockpit display, and validation gates.
- [ ] Every expensive run needs a cost/time estimate, worker count rationale, checkpoint/reuse plan, and abort rule.
- [ ] Rented compute should be driven near capacity. For a 256-vCPU host, the default target is at least `230` workers, reserving about 26 vCPUs for the OS, SSH, tmux, filesystem, logging, and monitoring. A lower worker count, such as `192`, requires a measured bottleneck or explicit owner acceptance.
- [ ] Every expensive run needs a progress-stall rule, such as no row advance for a declared interval while workers are present.
- [ ] Every imported result needs quarantine first, then validation of run id, expected work-unit count, active-run boundary, PBO/DSR/CSCV gates, and cockpit aggregation.
- [ ] JSONL row count is not completion. `universe_result.json` or the declared terminal artifact is completion.
- [ ] Python fallback or non-C++ realism must be labeled as research evidence, not production-realistic proof.
- [ ] Worker count is execution topology, not model/data identity. Checkpoints may be reused across worker-count changes only when all scientific identity fields still match.
- [ ] Source-hash changes during an active checkpoint are a hard reuse risk. Do not sync code into an active expensive run unless the checkpoint is backed up and the change is explicitly classified as metadata/control-only, with a migration note and post-restart reuse evidence.
- [ ] Tail units that take much longer than median units must be summarized by event, symbol, hypothesis count, elapsed seconds, and data size before rerunning.
- [ ] Compute scaling must be measured while still using paid capacity aggressively. More cores do not help if the bottleneck is Python startup, disk I/O, single long event windows, serialization, or hftbacktest per-event setup, but under-utilization is not acceptable without proof.
- [ ] Missing data must be converted into model/unit skip rules, not global stoppage, unless the missing data is a declared dependency of the model or opportunity unit being tested.
- [ ] Feature-plane drift must be rejected before another expensive run. A run
  that cannot prove PIT consumption of admitted cross-asset, VIX, options,
  macro-context, continuous/session, and latency features must be scoped down
  honestly instead of marketed as the full research product.

## Candidate Opportunity Families

These are candidate model families to test, not accepted alpha claims.

| Family | Research clock | Current data support | Source basis | Required proof |
|---|---|---|---|---|
| OFI / MLOFI short-horizon response | Continuous intraday and event windows | MBO NPZ and feature slots exist. | Vault `04 Order Flow Imbalance and Price Impact`; `specs/FEATURES.md` slots 50-51. | OOS folds, tick-regime stability, cost/latency stress, no in-sample-only selection. |
| Queue depletion / liquidity vacuum | Continuous intraday, post-event reaction | MBO event streams and regime slots exist. | Vault `03 MBO Event-Level Dynamics`; `05 Hawkes and Point Processes`; `specs/FEATURES.md` regimes 43 and 49. | Queue/fill calibration, adverse-selection markout, latency sensitivity. |
| Flow toxicity / VPIN | Continuous intraday and pre-event positioning | VPIN structural model exists. | Vault `01 Classical Market Microstructure`; `specs/FEATURES.md` slot 52. | Net performance after fees/slippage and toxicity-conditioned fill quality. |
| Hawkes / event-intensity state | Continuous intraday | Literature and partial structural models exist. | Vault `05 Hawkes and Point Processes`; Wu/Rambaldi/Muzy/Bacry note. | Per-symbol calibration, stationarity diagnostics, out-of-sample event-intensity validation. |
| Regime-conditioned models | All clocks | Regime posterior slots 41-49 exist. | `specs/FEATURES.md`; vault `System Implications.md`. | Compare unconditioned vs regime-conditioned results with ablation and multiple-testing control. |
| Macro context uplift | Context-feature uplift | Event calendar and Stage A event evidence exist. | `docs/cockpit/MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md`. | Target-only baseline versus target+context, PIT release proof, negative controls. |
| VIX/VVIX/VX volatility regime | Context-feature uplift and continuous | Volatility sensor rows exist; Stage A VIX coverage is mixed by event. | `docs/workbench/HOT_MEMORY_UNIVERSE.md`; vault `11 Options Microstructure.md`. | Coverage counts, missingness reason, source timestamps, uplift ablation. |
| CME options liquidity/gamma context | Context-feature uplift and options lane | Options data exists with known gaps. | O'Donovan/Yu/Zhang note; `specs/OPTIONS_LANE.md`. | PIT chain/quote proof, units, source ids, no structural-only promotion. |
| Options order imbalance | Options lane and context-feature uplift | Data readiness partial. | Lee/Ryu/Yang/Yu note. | Separate parity-arbitrage and informed-flow components or mark raw imbalance proxy-only. |
| Cross-asset lead-lag | Continuous intraday, event windows, context uplift | Lake coverage includes ES/MES/NQ/MNQ/ZN/ZB families; current feature path still needs true multi-symbol alignment evidence. | Vault `System Implications.md`; `specs/FEATURES.md`; `docs/workbench/HOT_MEMORY_UNIVERSE.md`. | Prove aligned multi-symbol MBO feature generation; reject placeholder/own-OFI behavior as real cross-asset evidence. |
| Energy inventory shock models | Event windows and continuous intraday | CL/NG/RB/HO are HOT/WARM symbols and EIA_CRUDE/EIA_NATGAS events exist; current `fs_v1` runnable feature set is not proven for all energy products. | `docs/workbench/HOT_MEMORY_UNIVERSE.md`; event universe EIA_CRUDE/EIA_NATGAS. | Data-doctor coverage, feature-build evidence for CL/NG, event timestamp proof, event and continuous variants measured separately. |
| Ghost route / stale micro routing | Continuous intraday and event windows | Queue-decay and micro/leader concepts exist in vault/model catalog; M6 artifacts are incomplete and many rows are zero-trade. | Vault Ghost Route model notes; `docs/workbench/MODEL_CATALOG.md`; vault `03 MBO Event-Level Dynamics.md`. | Calibrated fills, nonzero-trade accounting, sign/veto testing, and fresh certification. |
| WARM expansion screens | Continuous intraday and event windows | HOT/WARM registry and capture manifests exist for FX, energy, grains, and metals. | `docs/workbench/HOT_MEMORY_UNIVERSE.md`; `specs/DATA_LAKE.md`. | Treat as capture stream evidence until NPZ/feature/backtest artifacts prove runnable coverage. |

## Acceptance Gate For The Corrected Build

Before another all-scope or rented-compute run:

- [ ] A VectorBT/workbench screening pilot artifact exists before expensive replay.
- [ ] The screening artifact records `screening_backend=vectorbt`.
- [ ] The screening artifact records `vectorbt_version`.
- [ ] The screening artifact records `vectorbt_engine=rust|numba|auto`.
- [ ] The screening artifact records `screening_artifact_hash`.
- [ ] The screening artifact records candidate IDs and candidate reasons.
- [ ] The screening artifact records promoted IDs and promoted reasons.
- [ ] The screening artifact records rejected IDs and rejected reasons.
- [ ] The screening artifact records `no_lookahead_signal_shift_proof`.
- [ ] The screening artifact records `license_review`.
- [ ] The screening artifact records `workbench_run_id`, `feature_set_id`,
  `events_csv_hash`, `lake_manifest_hash`, and `created_at_utc`.
- [ ] M6 replay scope is selected/promoted IDs from the screening artifact, not a broad discovery sweep.
- [ ] The run declares exactly one or more research clocks from this spec.
- [ ] The run declares whether it is event-target, context-uplift, continuous, or mixed.
- [ ] The run has a pilot artifact with the same schema as the full run.
- [ ] The full run has expected work-unit count and can explain how that count is derived.
- [ ] The run declares host CPU count, reserved core count, requested worker count, and worker-utilization target. On a 256-vCPU rented host, requested workers must be at least `230` unless a measured cap is documented.
- [ ] The full run has a stall monitor and abort rule.
- [ ] The checkpoint preflight records whether any runner/source hash changed since the checkpoint was written. If yes, preserve a backup and require an explicit metadata-only migration note before reuse.
- [ ] The artifact records `model_feature_usage_status`, not only feature catalog eligibility.
- [ ] The artifact records `feature_plane_status` and a feature-usage manifest
  covering primary futures MBO / `fs_v1`, cross-asset futures, VIX/VVIX, VIX
  options, CME options, prior macro releases, continuous/session features, and
  latency state.
- [ ] Any unavailable feature family has a dependency-scoped
  `why_not_used_or_sidelined` reason and does not globally block unrelated
  available-data research.
- [ ] Context-feature claims include target-only baseline, target-plus-context result, delta after costs, PIT proof, and robustness state.
- [ ] Continuous intraday claims include decision timestamp/bucket, opportunity type, horizon, and execution assumption.
- [ ] Missing data sidelines only dependent models; it must not block unrelated available-data models.
- [ ] Cockpit labels event-window evidence, context-uplift evidence, continuous evidence, and options-lane evidence separately.

If any item fails, the run may be useful research, but it is not a full product
validation run and must not be shown as GREEN.
