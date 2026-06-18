# L3 Orphan Transformation

Real CME MBO captures may start mid-session. CANCEL/MODIFY/FILL events without a prior ADD in-window are orphans.

## Transformation

- Detection: L3 classification (ADD present, DEPTH absent)
- Filter: drop orphan CANCEL/MODIFY/FILL rows; keep all ADD and TRADE rows
- Applied once in `prepare_replay_data()`, not per scenario

## Evidence

`transformation_manifest.json` records:

- `removed_orphan_count`
- `removed_order_ids_sample` and hash
- validation before/after transformation

## Parity

Prepared stream must match legacy per-build filter in `hft_backtest_builder._filter_l3_orphans`.
