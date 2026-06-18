# VectorBT to HftBacktest Handoff

## Production path (strict)

```
research_cards/pipeline_runs/<run_id>/screening_artifact.json
  → validate_screening_artifact()
  → replay_eligibility_status == "eligible"
  → hft_generate_campaign_manifest.py
  → hft_run_campaign.py
```

## Dev/parity transitional path

```
vectorbt_filter.json + research_cards/promotion/*.json
  → transitional_handoff: true
  → certification_status: accelerated_not_certifying
```

Transitional artifacts cannot certify production replay.

## Immutable upstream evidence

- `screening_artifact_hash` pinned on every scenario
- HftBacktest campaign never modifies VectorBT screening results
