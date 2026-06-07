#!/usr/bin/env python3
"""Query chi404 manifest for costs."""
import pandas as pd
m = pd.read_parquet('/root/hft3/repo/data/manifest.parquet')
m = m.sort_values('end_utc', ascending=False)
recent = m[m['end_utc'] >= '2026-06-07 08:00:00']
print('rows since 08:00:', len(recent))
print('cost: $', round(recent['cost'].sum(), 2))
print('unique event_ids:', recent['event_id'].nunique())
zero = recent[recent['cost'] == 0]
print('zero-cost rows:', len(zero))
if len(recent) > 0:
    print('first 3 download_time:', recent['download_time'].head(3).tolist())
    print('last 3 download_time:', recent['download_time'].tail(3).tolist())
    # Cost per keepalive iteration (rows are per-download)
    print('total rows:', len(recent))
    print('total cost $:', round(recent['cost'].sum(), 2))
    print('avg cost per row: $', round(recent['cost'].mean(), 4))
