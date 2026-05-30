# E2E latency harness (tick → order ack)

**Primary path (2026):** R|Trader VM paper orders → SMB log bridge → `paper_latency_daemon` → `records.ndjson` / `latency_waterfall.json`. See [docs/rithmic_trial/README.md](../../../docs/rithmic_trial/README.md#paper-order-submitack-latency-authoritative).

**Status: BLOCKED** for production claims until ≥1,000 paired paper submit→ack samples (`order_ack_measured=true`).

## Purpose

Measure end-to-end latency on the production colo path:

1. **Tick receive** — `local_monotonic_receive_ns` at R|Trader bridge ingest (CHI404).
2. **Order submit** — R|Trader log parse (`rithmic_submit_mono_ns`).
3. **Order ack** — R|Trader log parse (`rithmic_ack_mono_ns`).

Report **submit→ack p50/p90/p99/p99.9** in microseconds; promote to `latency_summary.json` when paired_count ≥ 1,000.

## Requirements

- Run **only** on CHI404 (BLUEPRINT §4). No workstation or file-bridge in the hot path.
- Use R|Trader VM log export (not TCP connect as ack proxy).
- Clock: `time.perf_counter_ns()` / monotonic for deltas; chrony discipline validated separately.
- Quarantine: trial lane raw under `data/raw/rithmic_trial_live_capture/` — never production `data/npz/`.

## Trial order ack (authoritative when promoted)

Paper orders via R|Trader VM + automated sweep:

```bash
bash scripts/chi404_run_paper_latency_sweep.sh
python3 scripts/latency_probe/summarize_latency.py --run-id <probe_run_id> --include-trial-appendix
```

Optional: set `LATENCY_PROBE_TRIAL_CAPTURE=1` on `run_all.sh` to run step 3 automatically before summarize (adds ~30s+ VM dependency).

## Output (when R|API+ implemented)

- Raw: `runtime/latency_reports/raw/{RUN_ID}/e2e_order_ack.json`
- Fields: `samples`, `p50_ms`, `p95_ms`, `p99_ms`, `max_ms`, `errors[]`

## Integration

- `scripts/latency_probe/run_all.sh` will invoke the harness after R|API+ lands.
- `summarize_latency.py` will read authoritative `order_ack_p99_ms` and clear the `e2e_harness` BLOCKED section.
- Until then: see `trial_order_ack_appendix` in `runtime/latency_reports/latency_summary.json`.

## Lane mapping (authoritative order ack p99)

| Lane | Order ack p99 |
|------|----------------|
| 1 | < 2 ms (plus loaded cyclictest < 20 µs and worst network p99 < 500 µs) |
| 2 | 2–10 ms |
| 3 | 10–50 ms |
| 4 | 50 ms+ |
