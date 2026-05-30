# PDF_MODEL_4 hybrid replay — CPI_2024_09_11_TIGHT

- NPZ: `data/npz/MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz` (146184 events)
- Latency: 1.0 ms (workstation_gate_fallback_1ms)
- Latency note: Paper order submit→ack not measured; pass --latency-ms or run scripts/chi404_run_paper_latency_sweep.sh on CHI404. TCP connect is network health only — not used for execution latency.
- Queue model: LogProbQueueModel2
- Defensive mode: `hybrid_full` (use_ofi=True, use_vpin=True)

## Result

- steps: 3299116
- balance: -15017525.5
- fee: 0.0
- num_trades: 20966
- position: 2736.0

Dependencies: PDF_MODEL_1 (OFI) → PDF_MODEL_3 (VPIN from TRADE vol) → PDF_MODEL_4.
