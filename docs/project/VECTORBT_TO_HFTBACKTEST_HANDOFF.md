# VectorBT to HftBacktest Handoff

Status: historical / inactive. The active pipeline is HftBacktest-only per
[HFTBACKTEST_ONLY_PIPELINE_PLAN.md](HFTBACKTEST_ONLY_PIPELINE_PLAN.md), and writes
current active run evidence under `artifacts/hbt_runs/<run_id>/`.
`screening_artifact.json` and VectorBT promotion hashes are legacy handoff
evidence only unless an owner explicitly re-enables this path.

## Active path (current)

```
HftBacktest-compatible event data + initial snapshot
  -> HftBacktest data validation
  -> scripts/run_hftbacktest_only.py
  -> artifacts/hbt_runs/<run_id>/
  -> stats_summary.json + recorder_result.npz
  -> promotion_decision.json
```

## Legacy production path (inactive)

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
