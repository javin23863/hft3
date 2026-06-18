# Feature-recipe hash specification

**Schema:** `feature_recipe.v1`  
**Code:** `packages/research_pipeline/feature_recipe.py`  
**Phase:** 1 (contract + hash; VectorBT/HBT consumption proof in Phases 5–6)

## Purpose

One deterministic identity for the **feature-selection dimensions** of a candidate, separate from execution parameters (`signal_threshold`, holding period, stops). VectorBT and HftBacktest must reference the same hash before promotion to paid compute.

## Hash input

`compute_feature_recipe_hash()` canonicalizes JSON (sorted keys, compact separators) over the recipe dict **excluding**:

- `feature_recipe_hash`
- `frozen_at_utc`
- `candidate_id`
- `manifest_id`
- `generation_index`
- `parent_candidate_id`
- `proposal_reason`

Family blocks are sorted by family id before hashing.

**Algorithm:** SHA-256 hex digest (full 64 chars), same style as `feature_usage_manifest_hash`.

## Recipe contents

| Field | Description |
|-------|-------------|
| `schema_version` | `feature_recipe.v1` |
| `model_id` | Registry model slug |
| `target_symbol` | e.g. `MES` |
| `research_clock` | `scheduled_event`, `continuous_intraday`, `context_feature_uplift` |
| `target_event_id` | Scheduled event id when applicable |
| `feature_families` | Eight canonical families with `FamilyMetadata` rows |
| `interactions` | Approved interaction specs |
| `context_gates` / `regime_gates` | Declared gates |
| `target_horizon` | Optional horizon label |
| `execution_assumptions` | Strategy params (execution dimension — included in hash) |

## Family metadata fields

Each family row records:

```text
family_id, source_ids, source_timestamp, feature_availability_timestamp,
target_decision_timestamp, units, feature_version, missingness_state,
staleness_state, pit_proof, model_consumption_state,
why_not_used_or_sidelined, selected_features, source_symbols, lag_windows,
transformations, allowed_context_events, lookback_rules
```

Consumption states match `feature_plane.py`: `consumed`, `not_used`, `sidelined_missing_data`, `sidelined_scope`, `not_measured`.

## PIT validation

`validate_recipe_pit_timestamps()` rejects:

- `source_timestamp` or `feature_availability_timestamp` after `target_decision_timestamp`
- `consumed` with `missingness_state` in `{missing, malformed, proxy_only}`

## Candidate manifest freeze

Before VectorBT, `freeze_candidate_manifest()` writes `candidate_manifests.jsonl` with:

```text
candidate_id, feature_recipe_hash, feature_recipe, execution_assumptions,
split_scheme, code_commit, generation_id, manifest_hash, frozen_at_utc
```

Fields are immutable after `frozen_at_utc`.

## Equality gate (Phase 6)

```text
screening_artifact.promoted[].feature_recipe_hash
==
hftbacktest_scenario.feature_recipe_hash
```

Mismatch → fail closed.

## Backward compatibility

Candidates without `feature_recipe` still run; dedup falls back to `param_hash_from_dict(model_id, strategy_params)`.
