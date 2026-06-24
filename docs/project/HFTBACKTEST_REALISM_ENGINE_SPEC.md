# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology, hft3 authority PDFs, the official HftBacktest documentation, and the upstream `nkaz001/hftbacktest` repository; do not invent replay, latency, queue, fill, or market-impact methodology outside that authority.

# HftBacktest Realism Engine Spec

Status: planning-control specification for the execution-realism implementation
that follows the VectorBT screening engine.

This document defines how hft3 must use official HftBacktest as the source of
truth for tick/order-book replay, latency modeling, order/fill simulation, queue
position, and replay artifacts. It exists to prevent a second invented
backtester from being built inside hft3.

## Scope

HftBacktest is the downstream execution-realism engine. It consumes only
validated VectorBT screen-passed candidates and answers:

```text
Given the actual replay feed, fee model, latency model, queue/fill model,
order behavior, and hft3 risk constraints, does this candidate still survive?
```

It does not discover the full model universe. It does not replace VectorBT
screening. It does not prove live readiness by itself. It replaces the
repo-local replay universe path for this implementation. The retired hft3
entrypoints `run_event_universe.py`, `replay_matrix.py`, `ReplaySession`, and
`run_event_replay.py` must not be used, extended, or treated as fallback paths
for this VectorBT/HftBacktest work. HftBacktest produces replay evidence that
must still feed robustness, calibration, shadow, and risk gates.

For hft3 realism claims, Python orchestration is not enough. Queue/fill replay
must use official HftBacktest APIs, and latency/risk/feature hot-path evidence
must be backed by hft3 native C++ artifacts where hft3 owns the hot path
(`rithmic_gateway`, `risk_engine`, `features_engine/cpp`, and `engine`). A
workstation Python fallback can be labeled research-only, but it cannot produce
`execution_realism_pass`.

## Source Authority Map

| Source | URL or local path | Binding consequence |
|---|---|---|
| Upstream repository | https://github.com/nkaz001/hftbacktest | Implementation must use official HftBacktest APIs or a pinned upstream snapshot. Hand-rolled replacement replay engines are forbidden for this layer. |
| Official docs home | https://hftbacktest.readthedocs.io/en/latest/index.html | HftBacktest is for HFT/market-making backtesting with feed/order latency, queue-position fill simulation, L2/L3 order-book replay, multi-asset support, Python/Numba, and Rust components. |
| Data docs | https://hftbacktest.readthedocs.io/en/latest/data.html | Replay input must match HftBacktest event schema and timestamp-ordering rules. |
| Data validation API | https://hftbacktest.readthedocs.io/en/latest/reference/data_validation.html | Event-order and timestamp corrections must use documented validation utilities when needed. |
| Data utilities API | https://hftbacktest.readthedocs.io/en/latest/reference/data_utilities.html | Databento conversion and data utilities are preferred over custom conversion when applicable. |
| Latency models | https://hftbacktest.readthedocs.io/en/latest/latency_models.html | Feed latency, order-entry latency, and order-response latency must be separate fields/artifacts. |
| Order fill docs | https://hftbacktest.readthedocs.io/en/latest/order_fill.html | Replay must preserve HftBacktest fill assumptions, especially no market impact in market-data replay. |
| Level-3 tutorial | https://hftbacktest.readthedocs.io/en/latest/tutorials/Level-3%20Backtesting.html | CME MBO/L3 paths must use L3-aware semantics when L3 data is present; L2 queue estimation is not equivalent to MBO queue truth. |
| Accelerated backtesting tutorial | https://hftbacktest.readthedocs.io/en/latest/tutorials/Accelerated%20Backtesting.html | Accelerated mode is a speed/accuracy tradeoff and cannot certify queue-critical strategies unless the lost accuracy is explicitly accepted. |
| Backtester API | https://hftbacktest.readthedocs.io/en/latest/reference/backtester.html | Adapter code must use documented backtester/depth/order/state APIs rather than external emulation. |
| hft3 robustness spec | `docs/project/ROBUSTNESS_TESTING_SPEC.md` | HftBacktest evidence is one layer in the DSR/PBO/CSCV/walk-forward promotion chain. |
| hft3 VectorBT spec | `docs/project/VECTORBT_SCREENING_ENGINE_SPEC.md` | HftBacktest consumes validated screen-passed IDs; it is not broad discovery. |
| Vault certification ontology | Obsidian vault `validation/Backtester Certification.md`, `library/System Implications.md`, `library/03 MBO Event-Level Dynamics.md`, `library/06 Optimal Execution.md`, `library/10 HFT Market Design and Latency.md` | Queue/fill, cost, latency, adverse-selection, and market-design assumptions must be traceable to the vault/PDF ontology. |

## Non-Negotiable HftBacktest Assumptions

The implementation must encode these assumptions as artifact fields and tests:

1. **Market-data replay has no market impact.** HftBacktest replay does not let
   our simulated order alter the historical market. Large liquidity-taking
   orders can therefore be unrealistic and must fail or be labeled as
   `market_impact_not_modeled`.
2. **Latency is three-part.** Feed latency, order-entry latency, and
   order-response latency are different. A single scalar may be used only when
   the artifact says which parts it approximates.
3. **L2 and L3 are different evidence.** L2 queue models estimate position.
   L3/MBO replay can derive queue position from order data. A result may not
   label an L2 queue estimate as L3 queue truth.
4. **Accelerated backtesting sacrifices realism.** It may be used for speed
   diagnostics or non-certifying research, but not as a replay-realism pass for
   queue-critical strategies unless the missing queue/order-response assumptions
   are explicitly accepted in the run contract.
5. **Data order is part of correctness.** `exch_ts`, `local_ts`, event flags,
   and positive feed-latency rules are scientific inputs. Invalid order or
   timestamp correction gaps fail the replay artifact.
6. **Backtest/live discrepancy is an artifact, not a footnote.** Any later paper
   or live comparison must record how replay differed from observed outcomes.

## Source Lock Contract

Every HftBacktest-backed run must write a source lock before it writes results.

Required file:

```text
research_cards/hftbacktest_realism/<run_id>/hftbacktest_source_lock.json
```

Required fields:

```text
upstream_repo_url
upstream_commit_sha_or_tag
upstream_ref_verification_status
upstream_ref_verified_against
upstream_docs_url
docs_pages_used
python_package_name
python_package_version
rust_crate_version_or_not_used
installed_module_path
source_lock_created_at_utc
hft3_commit
hft3_adapter_files
api_surface_used
known_doc_repo_discrepancies
license_review
native_hot_path_required=true
native_hot_path_evidence
native_hot_path_status
```

Rules:

- Pin coordinates live in `vendor/hftbacktest/VENDOR.lock`. Install the pinned
  PyPI package with `bash scripts/install_hftbacktest_realism_deps.sh` before
  HBT realism tests or source-lock runs. The installed package version must
  match `upstream_commit_sha_or_tag` for `package_version_match` verification.
- If the installed package, upstream repo snapshot, and docs disagree, the run
  must record the discrepancy and the implementation must follow the installed
  API plus pinned upstream source for that run.
- If `upstream_commit_sha_or_tag` is missing, replay evidence is non-GREEN.
- If HftBacktest is unavailable and hft3 falls back to a local simulator, the
  result must be `hftbacktest_unavailable`, not `execution_realism_pass`.
- A valid source lock, and therefore any `pass` replay summary, requires native
  C++ hot-path evidence with a SHA-256-backed evidence marker. Bare
  recognizable paths are operator context only, not source-lock evidence.
- Native hot-path evidence values are receipt artifact references, not source
  file or binary references. Strict pass evidence must point under approved
  artifact roots such as `reports/cpp_lane/`, `runtime/reports/`,
  `runtime/latency_reports/`, or `research_cards/`, use an artifact suffix such
  as `.json`, `.jsonl`, `.log`, `.md`, `.parquet`, or `.txt`, include a
  recognized hft3 C++ hot-path token, and include `#sha256:<64-hex-digest>`.
  Legacy token-only or source/build paths such as
  `rithmic_gateway/tools/rithmic_latency_probe`, `scripts/run_c_lane.sh`, or
  `build/hft3_engine` are intentionally rejected with
  `native_cpp_hot_path_evidence_unrecognized`; regenerate or package them into a
  C-lane receipt artifact before strict replay eligibility.

## VectorBT Handoff Gate

Before HftBacktest replay can be considered eligible evidence, the selected
candidate row from `screening_artifact.json` must satisfy the VectorBT handoff
contract in `docs/project/VECTORBT_SCREENING_ENGINE_SPEC.md`: `screening_status`
must be `pass`, `replay_eligibility_status` must be `eligible`,
`robustness_artifact_staleness` must be `fresh`, and WFC, DSR, PBO, and CSCV
status fields must all be `pass` with non-`not_run` evidence. Missing,
malformed, stale, or failing robustness fields are fail-closed
`screening_artifact_replay_ineligible:*` reasons, even when official
HftBacktest replay itself completes successfully.

## Data Contract

HftBacktest input must be a structured event array with the documented event
fields:

```text
ev
exch_ts
local_ts
px
qty
order_id
ival
fval
```

Required validation before replay:

- event dtype matches expected HftBacktest dtype;
- `exch_ts` and `local_ts` units are nanoseconds;
- exchange-timestamp ordering is valid for `EXCH_EVENT` rows;
- local-timestamp ordering is valid for `LOCAL_EVENT` rows;
- local timestamp is not earlier than exchange timestamp after documented
  correction;
- feed latency correction, if applied, records method and base latency;
- L2/L3 classification is explicit: `l2_mbp`, `l3_mbo`, or `mixed_rejected`;
- CME MBO/L3 paths preserve `order_id` when required for queue evidence;
- orphan L3 events are rejected or filtered with counts and reasons.

Fail-closed statuses:

```text
EVENT_DTYPE_INVALID
EXCHANGE_ORDER_INVALID
LOCAL_ORDER_INVALID
NEGATIVE_FEED_LATENCY_UNCORRECTED
L3_ORDER_ID_MISSING
L2_L3_MISMATCH
EVENT_ARRAY_EMPTY
EVENT_TYPE_UNKNOWN
ORPHAN_L3_EVENTS_UNACCOUNTED
HFTBACKTEST_VALIDATE_EVENT_ORDER_FAILED
HFTBACKTEST_VALIDATE_EVENT_ORDER_UNAVAILABLE
DATA_NPZ_MISSING_DATA_ARRAY
DATA_NPZ_READ_FAILED
TIMESTAMP_UNITS_UNPROVEN
```

## Latency Contract

Each replay must choose exactly one latency model family:

| Model family | Allowed when | Artifact requirement |
|---|---|---|
| `ConstantLatency` | Only when measured or explicitly declared constants are intended. | Record `feed_latency_ms`, `order_entry_latency_ms`, `order_response_latency_ms`, component mapping, unit conversion, and native C++ probe provenance. |
| `IntpOrderLatency` | Preferred when real order-latency samples exist at adequate interval. | Record latency sample source, row count, `req_ts/exch_ts/resp_ts/_padding` schema, interpolation method, and native C++ probe provenance. |
| `FeedLatency` | Only when live order-latency data is unavailable. | Mark as synthetic/proxy, record `order_latency_unavailable_reason`, and return `latency_proxy_only`, not a final live-readiness status. |
| Custom model | Only when official HftBacktest custom-latency interface is used. | Record source citation, tests, native C++ latency provenance, and parity against a simple baseline. |

Required latency fields:

```text
latency_model_family
feed_latency_source
order_entry_latency_source
order_response_latency_source
latency_units
latency_value_or_sample_hash
latency_p50_ms
latency_p90_ms
latency_p99_ms
latency_source_authority
latency_proxy_status
latency_component_mapping
native_latency_probe_artifact
native_latency_probe_status
native_latency_probe_artifact_hash
native_latency_probe_provenance
native_latency_probe_host
```

For CME production-style claims, CHI404 native C++ latency artifacts remain the
hft3 authority. Workstation Python runtime is informational only. A measured
latency artifact must use millisecond fields and ordered percentiles
(`latency_p50_ms <= latency_p90_ms <= latency_p99_ms`). Bare path/status strings
are insufficient: measured/custom latency must carry a SHA-256 artifact hash,
`CHI404` host evidence, `latency_proxy_status=measured`, a SHA-256
`latency_value_or_sample_hash`, and `hft3_native_cpp_rithmic_latency_probe`
provenance. Source-lock native hot-path evidence must point at recognizable hft3
native C++ receipt/report artifacts under `reports/`, `runtime/`, or
`research_cards/`: latency artifacts (`rithmic_latency_probe` or
`reports/latency_baselines/`), feature parity artifacts (`hft3_features_cpp`,
`verify_cpp_parity`, `hft_feature_golden`, `hft_event_context_golden`), and
risk/safety/engine-loop gates (`test_decision_runtime_hardening`,
`test_safety_failure_injection`, `test_engine_loop`, `hft3_engine`, or the
named TSan stress targets). Source files, build products, and generic strings
such as `scripts/run_c_lane.sh`, `build/hft3_engine`, `evidence.json`, or
`risk_engine_fake_claim.json` are not sufficient.

Strict source-lock pass requires complete native evidence classes, not one
representative native artifact. The required classes are `latency`, `features`,
`risk_concurrency`, `decision_safety`, and `engine_loop`. `scripts/run_c_lane.sh`
writes hashable `reports/cpp_lane/` receipts for the feature, risk/concurrency,
decision/safety, and engine-loop classes only after all C-lane checks pass.
Those receipts must carry schema `hft3_cpp_lane_receipt_v1`, `status=pass`, the
expected check names, and the current `hft3_commit`; source-lock validation reads
the receipt content and rejects stale, mismatched, or filename-only evidence.
Latency still comes from a CHI404 native Rithmic latency probe/latency report
artifact whose content proves `hot_path_language=c++`, `wrapper=none`, and
`rithmic_latency_probe` provenance.

HBT-2 status precedence:

```text
hftbacktest_unavailable
fail                           # invalid source lock, invalid screening, invalid measured latency, or validator unavailable
data_invalid                   # invalid HftBacktest input data
latency_proxy_only             # valid FeedLatency proxy and no higher-severity failure
research_only                  # source/data/latency gates not fully replayed yet
```

## Fill And Queue Contract

Every replay must declare:

```text
exchange_model
queue_model
queue_model_source
fill_model_scope
partial_fill_policy
time_in_force_policy
maker_fee
taker_fee
tick_size
lot_size
minimum_order_qty
market_impact_mode=not_modeled|external_charge|rejected
```

Rules:

- `NoPartialFillExchange` and `PartialFillExchange` must be used according to
  their documented assumptions.
- Liquidity-taking orders that are large relative to displayed book depth must
  be rejected or marked `market_impact_not_modeled`.
- L3/MBO replay should use L3 queue semantics where HftBacktest supports them.
  L2 probability queue models are allowed only when the data path is L2/MBP or
  when an explicit L3-to-L2 comparison run is being performed.
- Queue model choice must be parameterized and recorded; hidden default queue
  models are forbidden.
- Fill evidence must include intended order, order response, actual fill,
  unfilled/cancelled state, queue metric, and markout.

## Adapter Contract

The hft3 integration layer must call documented HftBacktest APIs rather than
recreate them. It is new implementation work, not an extension of the retired
hft3 replay universe path.

Allowed adapter responsibilities:

- load and validate hft3 NPZ/MBO paths;
- build HftBacktest assets/configuration;
- map validated screen-passed candidates into HftBacktest-compatible algorithms;
- inject hft3 fees, latency, risk caps, tick/lot size, and order behavior;
- collect HftBacktest orders, fills, state values, and latency outputs;
- persist hft3 replay artifacts.

Forbidden adapter responsibilities:

- hand-roll order-book replay when HftBacktest can execute the path;
- simulate queue position outside HftBacktest and call it HftBacktest evidence;
- use a Python fallback as a PASS artifact;
- silently switch between L2/L3, exchange model, queue model, or latency model;
- route new work through `run_event_universe.py`, `replay_matrix.py`,
  `ReplaySession`, or `run_event_replay.py`;
- run broad model discovery from replay.

## Replay Artifact Contract

Required directory:

```text
research_cards/hftbacktest_realism/<run_id>/
```

Required files:

```text
hftbacktest_source_lock.json
input_manifest.json
data_validation.json
latency_model.json
fill_queue_model.json
official_replay.json
orders.jsonl
fills.jsonl
markouts.parquet_or_jsonl
discrepancies.json
discrepancy_comparison.json
replay_summary.json
```

Required `replay_summary.json` fields:

```text
run_id
created_at_utc
hft3_commit
screening_artifact_hash
candidate_id
model_id
symbol
research_clock
event_or_session_scope
hftbacktest_source_lock_hash
data_validation_status
latency_model_family
exchange_model
queue_model
queue_model_source
fill_model_scope
partial_fill_policy
time_in_force_policy
accelerated_mode=false
accuracy_tradeoff_declared
queue_position_modeled
order_response_latency_modeled
full_replay_comparison_hash_or_not_run
certification_allowed
market_impact_mode
orders_intended
orders_submitted
orders_acknowledged
orders_cancelled
fills_count
partial_fills_count
unfilled_count
fill_rate
avg_queue_position_or_not_available
latency_p50_ms
latency_p90_ms
latency_p99_ms
maker_fees
taker_fees
gross_pnl
net_pnl
execution_adjusted_expectancy
max_drawdown
adverse_selection_markout
spread_capture_or_cost
official_hftbacktest_replay_status
official_replay_artifact_hash
discrepancy_comparison_status
discrepancy_comparison_artifact_hash
certification_feedback_status
replay_realism_status
fail_closed_reasons
```

Allowed `replay_realism_status` values:

```text
pass
fail
research_only
hftbacktest_unavailable
data_invalid
latency_proxy_only
market_impact_not_modeled
accelerated_not_certifying
```

## Accelerated Mode Rule

Accelerated HftBacktest paths may be useful for faster iteration, but they must
not be confused with full replay certification.

Required artifact fields:

```text
accelerated_mode
accuracy_tradeoff_declared
queue_position_modeled
order_response_latency_modeled
full_replay_comparison_hash_or_not_run
certification_allowed=false
```

Certification may be considered only after a comparison artifact shows the
accelerated result is equivalent enough for the exact strategy class, symbol,
latency model, and queue/fill assumptions being tested.

## Implementation Milestones

Work must proceed in order.

### HBT-0: Source Map And Lock

- [x] Create HftBacktest source-lock schema
  (`packages/backtest_pipeline/src/hftbacktest_realism.py`).
- [x] Create the new official-HftBacktest realism runner entrypoint
  (`scripts/run_hftbacktest_realism.py`).
- [x] Block retired replay entrypoints from this implementation path.
- [x] Record upstream repo URL, commit/tag, docs pages, installed package version,
  and hft3 adapter files.
- [x] Add a test that refuses HftBacktest PASS artifacts without a source lock.
- [x] Reject downstream handoff when the terminal VectorBT artifact requires the
  Rust engine but reports a non-Rust or unavailable engine.

HBT-0 is source-lock and fail-closed handoff only. It must not mark
`replay_realism_status=pass`; HBT-1 through HBT-4 still supply data validation,
latency, fill/queue, and minimal replay artifacts before any execution-realism
claim can be GREEN.

### HBT-1: Data Validation Gate

- [x] Validate event dtype and timestamp ordering before replay
  (`packages/backtest_pipeline/src/hftbacktest_realism.py`,
  `tests/backtest_pipeline/test_hftbacktest_realism_hbt1.py`).
- [x] Record L2/L3 classification and orphan-event counts in
  `data_validation.json`.
- [x] Reject invalid or unaccounted data rather than silently correcting it.

HBT-1 writes `data_validation.json` and propagates data-validation status into
`replay_summary.json`. It still does not run a HftBacktest replay, configure
latency/fill/queue models, or certify execution realism.

### HBT-2: Latency Model Gate

- [x] Encode feed, order-entry, and order-response latency separately.
- [x] Support constant measured latency first with explicit component mapping.
- [x] Add interpolated order-latency support only when sample artifacts exist.
- [x] Mark feed-latency-derived latency as proxy-only.

HBT-2 writes `latency_model.json`, records latency fields in
`replay_summary.json`, and preserves `research_only` until HBT-3/HBT-4 add
fill/queue and minimal official replay evidence. Verification:
`python -B -m pytest -q tests\backtest_pipeline\test_hftbacktest_realism_hbt0.py tests\backtest_pipeline\test_hftbacktest_realism_hbt1.py tests\backtest_pipeline\test_hftbacktest_realism_hbt2.py -p no:cacheprovider`
-> 53 passed.

### HBT-3: Fill/Queue Model Gate

- [x] Explicitly configure exchange model, queue model, fee model, tick size, and
  lot size.
- [x] Reject or label market-impact-sensitive orders.
- [x] Record intended, submitted, acknowledged, cancelled, filled, and unfilled
  order states.

HBT-3 writes `fill_queue_model.json`, validates official HftBacktest exchange
and queue API choices, records maker/taker fees plus tick/lot/minimum order
quantity, rejects L2 probability queues as L3 truth unless the run is explicitly
an L3-to-L2 comparison, and labels unmodeled market-impact assumptions as
`market_impact_not_modeled`. Verification:
`python -B -m pytest -q tests\backtest_pipeline\test_hftbacktest_realism_hbt0.py tests\backtest_pipeline\test_hftbacktest_realism_hbt1.py tests\backtest_pipeline\test_hftbacktest_realism_hbt2.py tests\backtest_pipeline\test_hftbacktest_realism_hbt3.py -p no:cacheprovider`
-> 79 passed.

### HBT-4: Minimal Full Replay Artifact

- [x] Consume one validated VectorBT screen-passed candidate.
- [x] Run one non-accelerated HftBacktest replay through documented official
  APIs (`HashMapMarketDepthBacktest.wait_next_feed`, submit order,
  `wait_order_response`, `orders`, `state_values`, `cancel`, and
  `clear_inactive_orders`).
- [x] Emit the full replay artifact set, including `official_replay.json`,
  `orders.jsonl`, `fills.jsonl`, `markouts.jsonl`, and `discrepancies.json`.
- [x] Mark status fail-closed if the order intent, source lock, data validation,
  latency model, fill/queue model, official replay, or hash-backed native C++
  evidence required for a pass is missing.

HBT-4 writes the minimal official replay artifact and can mark
`replay_realism_status=pass` only when HBT-0 through HBT-4 all pass together:
Rust VectorBT handoff, valid source lock, valid HftBacktest input data,
separate latency model, explicit fill/queue model, non-accelerated official
HftBacktest replay, and hash-backed native C++ hot-path evidence. This is still
execution-realism evidence for a candidate, not live readiness, paper-routing
approval, or robustness certification. Verification:
`python -B -m pytest -q tests\backtest_pipeline\test_hftbacktest_realism_hbt0.py tests\backtest_pipeline\test_hftbacktest_realism_hbt1.py tests\backtest_pipeline\test_hftbacktest_realism_hbt2.py tests\backtest_pipeline\test_hftbacktest_realism_hbt3.py tests\backtest_pipeline\test_hftbacktest_realism_hbt4.py -p no:cacheprovider`
-> 91 passed.

### HBT-5: Calibration And Discrepancy Loop

- [x] Compare replay output against paper/live observation when available.
- [x] Record discrepancies by fill rate, latency, fees, slippage, markout, and
  order state.
- [x] Feed discrepancy results back into robustness/certification, not into
  hidden parameter tuning.

HBT-5 writes `discrepancy_comparison.json` from an optional offline
paper/live observation artifact supplied to `write_hftbacktest_realism_artifacts`
or `scripts/run_hftbacktest_realism.py --observation-artifact`. It does not
connect to paper/live systems and does not mutate candidate parameters. Missing
observation leaves `certification_feedback_status=blocked_missing_observation`
without retroactively failing HBT-4 replay realism; malformed or mismatched
observations fail closed into the replay summary and `discrepancies.json`.
Verification:
`python -B -m pytest -q tests\backtest_pipeline\test_hftbacktest_realism_hbt0.py tests\backtest_pipeline\test_hftbacktest_realism_hbt1.py tests\backtest_pipeline\test_hftbacktest_realism_hbt2.py tests\backtest_pipeline\test_hftbacktest_realism_hbt3.py tests\backtest_pipeline\test_hftbacktest_realism_hbt4.py tests\backtest_pipeline\test_hftbacktest_realism_hbt5.py -p no:cacheprovider`
-> 97 passed.

## Acceptance Gate

The HftBacktest realism layer is not accepted until all are true:

- [x] It uses official HftBacktest APIs or a pinned upstream source snapshot.
- [x] It writes `hftbacktest_source_lock.json` for every replay run.
- [x] It validates data schema and timestamp ordering before replay.
- [x] It records L2/L3 classification and does not label L2 estimates as L3 truth.
- [x] It records feed/order-entry/order-response latency assumptions separately.
- [x] It records exchange model, queue model, fill model, fees, tick size, and lot
  size.
- [x] It fails closed on unavailable HftBacktest, invalid data, unaccounted L3
  events, missing latency, or missing fill/queue fields.
- [x] It labels accelerated mode as non-certifying unless equivalence is proven.
- [x] It consumes only validated VectorBT screen-passed candidates.
- [x] It never claims live readiness from HftBacktest alone.

If any item fails, the result may be exploratory, but it is not accepted
execution-realism evidence.
