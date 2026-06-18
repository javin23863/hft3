# HftReplayScenario Schema

Frozen dataclass: `packages/backtest_pipeline/src/hft_campaign/scenario.py`

## Required execution fields

- Identity: `scenario_id`, `candidate_id`, `model_id`, `symbol`, `event_id`, `event_type`
- Upstream: `upstream_screening_artifact`, `upstream_screening_artifact_hash`
- Data: `prepared_data_path`, `prepared_data_hash`, `source_data_hash`
- Features: `feature_set_id`, `feature_set_hash`, `feature_timeline_hash`, `research_clock`
- Execution: `latency_model_path`, `latency_model_hash`, `fill_queue_model_path`, `fill_queue_model_hash`, `fee_model_id`, `execution_policy_id`
- Run: `replay_mode`, `replay_tier`, `stepping_mode`, `seed`, `split_scheme_id`

## Scenario ID

`{candidate_id}__{event_id}__{sha256(execution_fields)[:16]}`

Any execution-relevant input change must change `scenario_id` and `scenario_hash`.
