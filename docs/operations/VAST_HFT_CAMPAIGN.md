# Vast HftBacktest Campaign Operations

## Worker count

Benchmark HftBacktest separately from VectorBT. Do not default workers to CPU count or VectorBT settings.

Start with `--workers 12` on laptop; scale on rented hosts only after screening artifact validates.

## Commands (on Vast host)

```bash
python scripts/hft_validate_replay_inputs.py --screening-artifact ... --event-id ... --source-npz ... --latency-model ... --fill-queue-model ...
python scripts/hft_prepare_replay_data.py --event-id ... --symbol MES --source-npz ...
python scripts/hft_generate_campaign_manifest.py --screening-artifact ... --select-all-replay-eligible --out ...
python scripts/hft_run_campaign.py --manifest ... --campaign-id ... --workers 12 --resume
python scripts/hft_validate_campaign_artifacts.py --campaign-id ... --manifest ...
```

## Artifacts sync

Mirror `artifacts/hftbacktest_campaigns/` to workstation for cockpit validation.

See also `runtime/monitor/universe_M6_full_watch.md` for legacy universe watch protocol (retired for certification).
