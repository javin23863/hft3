# PDF_MODEL_4 hybrid pipeline gate

Repeatable end-to-end proof that the hybrid structural stack backtests on real Databento MBO.

## Definition of done

One **PDF_MODEL_4** hybrid model fully backtested on a single macro event window:

- NPZ present (`data/npz/`)
- Unit tests green (strategy + defensive toggles)
- Four defensive ablation modes run (unless `--skip-ablation`)
- `ReplayRunner` + `HybridExecutionStrategy` completes without error
- Research card written under `research_cards/PDF_MODEL_4_hybrid_replay/`
- Optional LLM after-action under `research_cards/PDF_MODEL_4_hybrid_pipeline/`
- Gate manifest `runtime/reports/hybrid_pipeline_gate.json` status **PASS**
- `--min-trades 1` on hybrid_full (default)

## Run (workstation)

```bash
python scripts/run_hybrid_pipeline_gate.py
```

Options:

```bash
python scripts/run_hybrid_pipeline_gate.py --event-id CPI_2024_09_11_TIGHT --symbol MES.v.0
python scripts/run_hybrid_pipeline_gate.py --latency-ms 1.0
python scripts/run_hybrid_pipeline_gate.py --skip-unit-tests
python scripts/run_hybrid_pipeline_gate.py --skip-ablation          # ~5 min vs ~25 min
python scripts/run_hybrid_pipeline_gate.py --skip-after-action      # no Ollama
python scripts/run_hybrid_pipeline_gate.py --min-trades 0           # replay-only check
```

## Steps

| Step | What |
|------|------|
| `preflight` | `events.csv` row + NPZ on disk (ES fallback OK) |
| `unit_tests` | `test_pdf_hybrid_strategy`, `test_pdf_defensive_ablation` |
| `defensive_ablation` | 4 modes: `as_baseline`, `ofi_only`, `vpin_only`, `hybrid_full` |
| `hybrid_backtest` | Primary `hybrid_full` card → `PDF_MODEL_4_hybrid_replay/` |
| `after_action_report` | AAR artifacts + optional Hawkish-8B narrative |
| `validate_card` | `result.json` + `report.md`; `num_trades >= --min-trades` |

## Latency

- **CHI404 colo:** uses measured paper order submit→ack from `runtime/latency_reports/latency_summary.json` when available.
- **Workstation gate:** falls back to **1.0 ms** inside blueprint band `[0.5, 10]` when order ack is unmeasured; recorded in gate manifest `gate_latency_note`.

Production promotion still requires CHI404 measured ack — see [PDF_HYBRID_REPLAY.md](PDF_HYBRID_REPLAY.md).

## Outputs

| Path | Purpose |
|------|---------|
| `research_cards/PDF_MODEL_4_hybrid_replay/result.json` | Primary hybrid_full replay payload |
| `research_cards/PDF_MODEL_4_hybrid_replay/report.md` | Primary human summary |
| `research_cards/PDF_MODEL_4_defensive_ablation/result.json` | Four-mode ablation matrix |
| `research_cards/PDF_MODEL_4_defensive_ablation/report.md` | Ablation comparison table |
| `research_cards/PDF_MODEL_4_hybrid_pipeline/` | AAR artifacts + `after_action_report.md` |
| `runtime/reports/hybrid_pipeline_gate.json` | PASS/FAIL + step log + `ablation_summary` |

## After-action (quote-engine)

Hybrid replay uses `execution_assumptions: quote_engine`. Per-trade `trades.parquet` audit is not emitted; AAR uses aggregate metrics with `audit_waiver_reason: quote_engine_aggregate_only` (discovery gate only — see [AFTER_ACTION_REPORTS.md](../workbench/AFTER_ACTION_REPORTS.md)).

## Related

- [PDF_HYBRID_REPLAY.md](PDF_HYBRID_REPLAY.md) — engine details
- [RESEARCH_ENTRYPOINTS.md](../vault/RESEARCH_ENTRYPOINTS.md) — canonical entry order
- Full 55-model catalog gate (PR2): `FULL_PIPELINE_GATE.md` (planned)
