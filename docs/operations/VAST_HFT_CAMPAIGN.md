# Vast HftBacktest Campaign Operations

## Current campaign state

Last checked: `2026-07-01T19:38Z` (`2026-07-02` Bangkok).

Canonical paid instance is Vast `42609000`. Do not substitute the
tandem/candidate instance `43298099`, and do not rent or start a second paid
host unless the owner explicitly changes the plan. At the last check, Vast
listed only `42609000`, with `actual_status=running`, `cur_state=running`,
`intended_status=running`, CPU about `84%`, and
`instance.totalHour=$0.6988888889/hr`.

Active campaign identity:

- Campaign root: `/data/hbt_vast_20260629_587e7f2`
- Manifest: `/data/hbt_vast_20260629_587e7f2/campaign_parameter_surface.jsonl`
- Output root:
  `/data/hbt_vast_20260629_587e7f2/hbt_full_parameter_surface_runs_078fa690`
- Status: `hbt_full_parameter_surface_078fa690.status.json`
- Monitor: `hbt_full_parameter_surface_078fa690.monitor_status.json`
- Watchdog: `hbt_full_parameter_surface_078fa690.watchdog_status.json`
- Resume start: `2026-07-01T15:29:59Z`
- Runner source commit: `a6424fc1095cff9c5eff23ceef948b6841df0518`
- Workers: `217`
- `max_tasks_per_child=256`

Tmux sessions expected on the active host:

- `hbt_full_078fa690_resume_event_scan_v2`
- `hbt_full_078fa690_85pct`
- `hbt_full_078fa690_monitor`
- `hbt_full_078fa690_watchdog`

Current issue: monitor reports `phase=progress_stalled`,
`3,123,439 / 3,950,895` row receipts, `827,456` remaining,
`rows_per_second_last_interval=0.0`, and no receipt advance for about four
hours. Watchdog remains `status=watching` with tmux sessions present and
manifest scan advancing around `13.08%`. The post-latest-resume log audit is
`post_resume_clean`; raw log tails can include old pre-marker
`QUANTUM_SPREAD_DEFENSE` / `numpy.trapz` lines. Treat the issue as paid resume
catch-up/manifest scanning before new row receipts, not as a crash,
completion, or permission to start another paid instance.

## Current status checks

Run from the canonical repo on the workstation:

```powershell
vastai show instances-v1 --raw
vastai ssh-url 42609000
```

Use the current SSH URL returned by Vast. The direct endpoint observed at the
last check was `root@211.21.106.81 -p 36849`, but ports can change after stops
or starts.

On the Vast host:

```bash
cd /data/hbt_vast_20260629_587e7f2
jq . hbt_full_parameter_surface_078fa690.status.json
jq . hbt_full_parameter_surface_078fa690.monitor_status.json
jq . hbt_full_parameter_surface_078fa690.watchdog_status.json
tmux ls
```

For log interpretation, prefer the post-resume audit:

```bash
cat hbt_full_parameter_surface_078fa690.post_resume_log_audit.json
```

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
