# Prepared Replay Data Specification

Content-addressed directory:

```
artifacts/hftbacktest_prepared_data/<prepared_data_hash>/
  events.npz
  source_manifest.json
  transformation_manifest.json
  validation.json
  preparation_metrics.json
  .complete
```

## Hash inputs

- source file hash, lake manifest hash
- event normalization version, L2/L3 classification version
- orphan handling implementation hash
- timestamp unit declaration, symbol, event_id
- HftBacktest event dtype version, hft3 commit

## Builder integration

`build_hftbacktest(..., prepared_data=True)` skips L3 orphan filtering on hot path.

CLI: `python scripts/hft_prepare_replay_data.py --event-id ... --symbol ... --source-npz ...`
