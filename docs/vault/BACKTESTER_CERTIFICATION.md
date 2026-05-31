# Backtester certification governance

Tiered verification for the replay/backtest stack. Research runs always proceed; stamps label trust level. Merge and champion promotion are gated separately.

## Tiers

| Tier | When | Command | Blocks merge? | Blocks research? | Blocks champion? |
|------|------|---------|---------------|------------------|------------------|
| **T0 Fast** | Every commit / PR | `python -m pytest tests/backtester_validation/fast -q` | Yes | No | Yes (via T4) |
| **T1 Stamp** | Every backtest / replay / workbench run | Automatic (`build_certification_stamp()`) | No | No | Labels only |
| **T2 Full** | Weekly / manual / after core engine change | `bash scripts/run_backtester_certification_full.sh` | No | No | Required GREEN for T4 |
| **T3 Staleness** | On every stamp + promotion check | `hft3.validation.certification_staleness` | No | Marks RESEARCH_ONLY | Yes if stale |
| **T4 Champion** | Before promotion | `bash scripts/check_champion_promotion_gate.sh` | No | No | Yes |

## Quick commands

```bash
# T0 — required before merge (also: bash scripts/run_backtester_fast_gate.sh)
python -m pytest tests/backtester_validation/fast -q

# T2 — full certification + scorecard
bash scripts/run_backtester_certification_full.sh

# T3 — print registry + staleness (exit 1 if stale/missing GREEN)
bash scripts/check_backtester_certification_status.sh

# T4 — champion promotion gate
bash scripts/check_champion_promotion_gate.sh --event-id CPI_2024_09_11_TIGHT --symbol ES --latency-ms 1.0 --queue-model LogProbQueueModel2
```

## Artifacts

| Path | Purpose |
|------|---------|
| `runtime/validation/certification_registry.json` | Latest GREEN/YELLOW/RED full cert |
| `runtime/validation/fast_gate_report.json` | Last T0 run |
| `runtime/validation/backtester_certification_scorecard.json` | T2 scorecard |
| `runtime/validation/champion_promotion_gate_report.json` | T4 gate result |

## Stamp fields

Every integrated result writer embeds `certification_stamp` and `certification_footer`:

- `scripts/run_event_replay.py`
- `replay/replay_session.py`
- `backtest_pipeline/src/research_runner.py`
- `workbench/src/run/engine.py`
- `workbench/src/run/campaign_runner.py` (`promote_candidate` requires `promotion_eligible`)
- `backtest_pipeline/src/pipeline_hyp_fanout.py`
- `scripts/run_pdf_hybrid_replay.py` (`execution_adapter_mode: legacy_hbt_callback`)

### Promotion labels

| Registry | Core changed? | Label |
|----------|---------------|-------|
| GREEN | No | `PROMOTION_ELIGIBLE_FROM_BACKTESTER_SIDE` |
| GREEN | Yes | `STALE_CERTIFICATION` / RESEARCH_ONLY |
| YELLOW | any | `RESEARCH_ONLY` |
| RED | any | `NOT_TRUSTED` |
| missing | any | `UNCERTIFIED` |

## CI

- `.github/workflows/backtester_fast_gate.yml` — T0 on every push/PR to `main`
- `.github/workflows/backtester_certification_scheduled.yml` — T2 weekly (Sunday 06:00 UTC) + manual dispatch

## Core engine paths (T3 staleness)

Defined in `hft3/validation/core_engine_paths.py`. Any change since last GREEN full cert marks stamps `STALE` until T2 is re-run.

## Package layout

```
hft3/validation/
  certification_registry.py
  certification_staleness.py
  research_stamp.py
  core_engine_paths.py
  certification_runner.py
  fast_gate_report.py
  promotion_gate.py

tests/backtester_validation/
  fast/   # T0
  full/   # T2
```

See also: [RESEARCH_ENTRYPOINTS.md](RESEARCH_ENTRYPOINTS.md) section 1.
