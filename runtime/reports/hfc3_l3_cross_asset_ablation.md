# HFC3 L3 cross-asset ablation

Generated: 2026-05-30T15:55:26.649511+00:00

| event_id | group | features | targets | mean |abs| return | verdict |
|----------|-------|----------|---------|----------------------|---------|
| CPI_2024_09_11_TIGHT | baseline_a_event_only | 0 | 0 | nan | noise |
| CPI_2024_09_11_TIGHT | baseline_b_event_plus_target_mbo | 1 | 1 | 0.002118 | noise |
| CPI_2024_09_11_TIGHT | cross_equity_mbo | 1 | 1 | 0.002118 | noise |
| CPI_2024_09_11_TIGHT | cross_rates_mbo | 1 | 1 | 0.002118 | noise |
| CPI_2024_09_11_TIGHT | cross_metals_mbo | 1 | 1 | 0.002118 | noise |
| CPI_2024_09_11_TIGHT | cross_energy_mbo | 1 | 1 | 0.002118 | noise |
| CPI_2024_09_11_TIGHT | cross_fx_mbo | 1 | 1 | 0.002118 | noise |
| CPI_2024_09_11_TIGHT | cross_vol_sensors | 0 | 0 | nan | insufficient_data |
| CPI_2024_09_11_TIGHT | cross_full_hot | 1 | 1 | 0.002118 | noise |
| CPI_2024_09_11_TIGHT | cross_warm_event_triggered | 1 | 1 | 0.002118 | noise |