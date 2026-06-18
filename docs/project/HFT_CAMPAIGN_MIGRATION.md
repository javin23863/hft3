# HftBacktest Campaign Migration and Rollback

## Feature flag

`HFT3_CAMPAIGN_RUNNER=legacy|campaign` during cutover.

## Rollback

Legacy paths remain for parity: `replay_matrix`, `run_event_replay.py`. M6/cockpit certification blocked on legacy per `docs/cockpit/CME_M6_SWEEP_CONTROL_PLAN.md`.

## Cutover criteria

- Parity corpus green (with hftbacktest installed)
- Core unit tests green
- Identical-scope benchmark shows wall-clock improvement
- Dual-pass reviewer merge-ready

## Benchmark

`python scripts/benchmark_hft_campaign.py`
