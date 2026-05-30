# PDF_MODEL_4 hybrid replay (real Databento MBO only)

Trial run of **PDF_MODEL_1 → PDF_MODEL_3 → PDF_MODEL_4** inside the hftbacktest `ReplayRunner` loop with queue-realistic LIMIT fills.

## Prerequisites

Real MBO NPZ from the trusted Databento lake (not synthetic):

```bash
python scripts/run_offline_pipeline.py --skip-download --event-id CPI_2024_09_11_TIGHT
```

Expected file:

`data/npz/MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz`

## Dependency chain

1. **PDF_MODEL_1** — BBO OFI / book pressure (`update_bbo` each step)
2. **PDF_MODEL_3** — VPIN from **TRADE** volume synced to `hbt.current_timestamp` (not bid+ask depth qty)
3. **PDF_MODEL_4** — Avellaneda–Stoikov hybrid quotes using MODEL_1 + MODEL_3 outputs

See [MODEL_DEPENDENCY_MAP.md](MODEL_DEPENDENCY_MAP.md).

## Defensive ablation (plug-and-play)

Toggle OFI and VPIN inputs without changing `HybridExecutionModel`:

| mode | use_ofi | use_vpin |
|------|---------|----------|
| `as_baseline` | false | false | Pure Avellaneda-Stoikov |
| `ofi_only` | true | false | OFI drift only |
| `vpin_only` | false | true | AS + VPIN-scaled lambda (unit OFI probe) + toxic flags; no book OFI |
| `hybrid_full` | true | true | Full hybrid |

**vpin_only honesty:** Hybrid drift is `lambda * (1+VPIN) * OFI_smooth`. With book OFI off, `ofi_smooth=0` would zero drift. Ablation passes `ofi_smooth=1.0` as a **unit probe** so VPIN lambda scaling is observable without book OFI.

Single run:

```bash
python scripts/run_pdf_hybrid_replay.py --no-ofi --no-vpin   # AS baseline
python scripts/run_pdf_hybrid_replay.py --no-vpin            # OFI only
```

Four-way matrix (real NPZ; ~4x replay runtime):

```bash
python scripts/run_pdf_hybrid_ablation.py --event-id CPI_2024_09_11_TIGHT
```

Default latency uses CHI404 measured summary (`runtime/latency_reports/latency_summary.json`); override with `--latency-ms`.

Output: `research_cards/PDF_MODEL_4_defensive_ablation/result.json` with PnL/trades/diagnostics per mode.

## Run

```bash
python scripts/run_pdf_hybrid_replay.py --event-id CPI_2024_09_11_TIGHT
```

Options: `--npz`, `--chi404-summary`, `--latency-ms`, `--queue-model`, `--step-ns`, `--out`.

Default latency uses CHI404 measured summary (same as ablation); override with `--latency-ms`.

Alternate entry (CHI404 latency from summary):

```bash
python scripts/run_event_replay.py --event-id CPI_2024_09_11_TIGHT --engine pdf_hybrid
```

Output default: `research_cards/PDF_MODEL_4_hybrid_replay/result.json` and `report.md`.

## Event window and quotes

- `time_remaining` uses `start_utc` and `end_utc` from event meta. Before the window starts, remaining time is the full window length (not an inflated AS horizon).
- **Quote refresh:** resting quotes are refreshed only when optimal bid/ask move by at least one tick, or every `quote_refresh_ticks` steps (default 10), reducing cancel/resubmit noise.
- **Split feeds (documented in ablation `eval_scope`):** PDF_MODEL_1 OFI and AS mid use `hbt.depth` BBO each step; PDF_MODEL_3 VPIN ingests MBO TRADE volume synced to replay time. VPIN mid uses internal MBO book mid when the book is valid; falls back to `hbt.depth` mid only when the internal book has no BBO.

## Ablation metrics

| field | meaning |
|-------|---------|
| `net_pnl` | Ending balance from replay |
| `net_pnl_after_fee` | `balance - fee` |
| `backtest_latency_note` | TCP p99 proxy; order ack not measured until Stage 3 |
| `chi404_measured_speed` | CHI404 probe payload when latency defaults from summary |
| `cancel_count` | Resting order cancels |
| `quote_refresh_count` | Quote refresh cycles |
| `mean_vpin` / `mean_ofi_smooth` | Step means from strategy diagnostics |

## Tests

```bash
pytest tests/backtest_pipeline/test_pdf_hybrid_strategy.py tests/backtest_pipeline/test_pdf_defensive_ablation.py -q
pytest tests/integration/test_pdf_hybrid_replay.py -q -m integration
pytest tests/integration/test_pdf_hybrid_ablation.py -q -m integration
```

Integration tests skip with instructions when NPZ is absent.

## Honest limits

- Python hftbacktest path — **not** the C++ production hot path ([HOT_PATH_AUDIT.md](../workbench/HOT_PATH_AUDIT.md))
- Results depend on `latency_ms`, `step_ns`, and queue model (`LogProbQueueModel2` default)
- `passive_to_aggressive_flag` crosses at BBO when VPIN toxicity is high; otherwise posts at optimal bid/ask
