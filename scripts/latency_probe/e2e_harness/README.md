# E2E latency harness (tick → order ack)

**Status: BLOCKED** until R|API+ is wired on CHI404 bare metal.

## Purpose

Measure end-to-end latency on the production colo path:

1. **Tick receive** — local monotonic timestamp when the market data tick arrives on CHI404.
2. **Order submit** — timestamp immediately before `submit` returns on the Rithmic session.
3. **Order ack** — timestamp when the exchange/broker ack event is received.

Report **submit→ack p99** in milliseconds for lane classification (lanes 1–4 in `summarize_latency.py`).

## Requirements

- Run **only** on CHI404 (BLUEPRINT §4). No workstation or file-bridge in the hot path.
- Use R|API+ (not R|Trader UI automation) for submit and ack events.
- Clock: `CLOCK_MONOTONIC` for deltas; chrony discipline validated separately.
- Quarantine: trial lane raw under `data/raw/rithmic_trial_live_capture/` — never production `data/npz/`.

## Trial order ack (appendix only, available today)

You can send **paper orders** via R|Trader Pro in the CHI404 Windows VM without R|API+. That path is measured separately and does **not** change the colo `recommended_lane`.

**Populate the appendix:**

1. VM running, R|Trader logged in (paper).
2. Send a few paper limit orders in R|Trader (manual).
3. Run live capture on CHI404:

```bash
bash scripts/chi404_run_trial_live.sh
```

4. Run the colo probe (appendix reads latest `reports/rithmic_trial/.../latency_profile.json`):

```bash
LATENCY_PROBE_CYCLICTEST_SEC=60 bash scripts/latency_probe/run_all.sh
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
