# CPI_2024_09_11_TIGHT baseline (2026-05-30)

Research baseline for macro event replay + CHI404 colo latency. **Canonical script order:** [RESEARCH_ENTRYPOINTS.md](RESEARCH_ENTRYPOINTS.md)

## Event

| Field | Value |
|-------|--------|
| event_id | `CPI_2024_09_11_TIGHT` |
| release_date | 2024-09-11 08:30 ET |
| window UTC | 2024-09-11T12:29:30Z → 2024-09-11T12:35:00Z |
| symbol | MES.v.0 |
| MBO NPZ | `data/npz/MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz` (146,184 events; gitignored data lake) |

## CHI404 measured speed (authoritative)

Source: `runtime/latency_reports/latency_summary.json` — probe `20260530T031754Z`

| Metric | Value |
|--------|--------|
| CPU loaded cyclictest p99 | 11 µs |
| Gateway ping p99 | 0.166 ms |
| Rithmic TCP 65000 p99 | **4.094 ms** (backtest latency default) |
| Order ack p99 | not measured (R\|API+ / Stage 3 broker harness pending) |

## Dual backtest engines (do not conflate)

| Engine | Script path | CPI result |
|--------|-------------|------------|
| **event_accurate_mbo** (primary research) | `SignalBacktester` via `scripts/run_event_replay.py` | 9/39 hyps traded; 3,506 trades total; HYP_5 = 11 trades, -$194.28 |
| **hftbacktest_loop** (queue-realistic) | `ReplayRunner` + MBO-synced `CombinedHypothesisStrategy` | Slow (~30+ min); use `--skip-hftbacktest` for fast reports |

Artifacts:

- `research_cards/CPI_2024_09_11_TIGHT_replay/result.json`
- `research_cards/CPI_2024_09_11_TIGHT_replay/report.md`
- `research_cards/single_run_CPI_HYP5/result.json` (HYP_5 only, 11 trades)

## Zero-trades fix (2026-05-30)

Old `ReplayRunner` path used depth-only features + mean@0.25 → 0 trades on CPI. Fixed:

- `CombinedHypothesisStrategy`: MBO pipeline synced to `hbt.current_timestamp`, `max_abs` aggregation, threshold 0.15
- `run_event_replay.py`: reports both engines; `primary_research_engine = event_accurate_mbo`

## Commands

```bash
# Fast CPI replay report (research path)
python scripts/run_event_replay.py \
  --event-id CPI_2024_09_11_TIGHT \
  --chi404-summary runtime/latency_reports/latency_summary.json \
  --skip-hftbacktest

# Single hypothesis (HYP_5)
python scripts/run_single_hyp_backtest.py \
  --chi404-summary runtime/latency_reports/latency_summary.json

# Sync CHI404 trial + latency to workstation
bash scripts/chi404_sync_trial_data.sh

# CHI404 broker trial with macro event tag
EVENT_ID=CPI_2024_09_11_TIGHT bash scripts/chi404_run_trial_capture.sh
```

## Rithmic trial lane (CHI404 only)

- Broker capture: `data/raw/rithmic_trial_capture/YYYY-MM-DD/` (capture date ≠ event_id)
- Manifest now supports `--event-id` for macro tagging
- No historical Rithmic download for Sept 2024; CPI MBO body = Databento NPZ

## Open next

1. Stage 1–3: R\|Trader broker verification + order-ack latency on CHI404
2. Populate `hftbacktest_loop` in report (optimize or overnight run)
3. R\|API+ connector swap when approved
