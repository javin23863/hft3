# Audit Friction Report

Graph-assisted layer audit (May 2026). Tracks findings and remediation status across hft3.

## Summary

| Phase | Scope | Status |
|-------|-------|--------|
| A | Orchestration glue | **Fixed** |
| B | CHI404 gates + rithmic tests | **Fixed** |
| C | Math integrity (lookahead, WF, leakage) | **Fixed** |
| D | Python/C++ parity + quarantine | **Mostly fixed** — regime 41–49 still Python-pipeline only in C++ |

## Phase A — Orchestration (fixed)

- `scripts/run_offline_pipeline.py`: correct `run_all_research_cards` import; guard missing NPZ; skip Databento when `--rithmic-trial` succeeds
- `backtest_pipeline/src/research_runner.py`: emits `matrix_smoke.json` alongside `all_hypotheses.json`
- `telemetry/src/dashboard.py`: package import `telemetry.src.metrics`
- `data_system/requirements.txt`: added `numpy`, `hftbacktest`

## Phase B — Honest gates (fixed)

- `infrastructure/chi404/validate_pass_criteria.py`: fail-closed when `mpstat`/`cpupower`/`chronyc` missing; strict jitter parse; G7 ring unknown fails; manifest non-empty required
- `infrastructure/chi404/04_irq_net_tuning.sh`: ethtool failure exits non-zero
- `HFT3_REPO_DIR` canonical: `/root/hft3/repo` in `remote.env.example`, `05_jitter_gate.sh`, `run_chi404_validate_remote.sh`
- `scripts/deploy_chi404_env.py`: deploys `HOT_CPUS`
- `scripts/run_chi404_tuning_remote.ps1`: derives repo path from script location
- `tests/test_rithmic_trial_pipeline.py`: strict pass assertions, canonical NPZ path, conversion report check

## Phase C — Math integrity (fixed)

- `backtest_pipeline/src/signal_backtester.py`: deferred fills at `signal_time + latency` (no same-tick future price lookahead)
- `decision_engine/python/src/train.py`: fail walk-forward on empty period; refuse export when WF not PASS; drop NaN labels
- `decision_engine/python/src/targets.py`: real leakage audit with forward-index checks
- `data_system/rithmic_trial/pipeline.py`: `process` fails on conversion/book fail; `replay-sample` uses combined strategy

## Phase D — Production parity (mostly fixed)

- `features_engine/cpp/src/feature_extractor.cpp`: TOP_3/5, absorption, iceberg, reload, MODIFY, 1s window reset, ratio parity
- `features_engine/cpp/include/feature_index.hpp`: regime enum slots 41–49 (filled by Python pipeline only)
- `decision_engine/cpp/src/decision_runtime.cpp`: full dot product inference
- `features_engine/src/features/feature_index.py`: hypothesis auxiliary slots 27–34 mapped
- `features_engine/src/pipeline/market_state_pipeline.py`: computes breaking-level and round-number proxies
- `features_engine/src/hypotheses/registry.py`: cross-asset hyps 16–20 excluded unless `HFT3_CROSS_ASSET=1`
- `data_system/rithmic_trial/config.py`: runtime quarantine guard vs `data/npz/`
- `data_system/rithmic_trial/convert/hftbacktest_converter.py`: fail on any depth event
- `data_system/rithmic_trial/connector/__init__.py`: clear error for unimplemented `rithmic_api`
- `features_engine/src/features/npz_feed.py`: NPZ schema validation
- `decision_engine/python/src/feature_store.py`: leakage audit before parquet write
- `backtest_pipeline/src/hft_strategy.py` + `runner.py`: latency band passed through

## Remaining known limitations

| Item | Notes |
|------|-------|
| C++ regime probs 41–49 | Ported in `RegimeFilterCpp`; written by `FeatureExtractorCpp` and Python pipeline |
| Cross-asset hyps 16–20 | Disabled by default; enable with `HFT3_CROSS_ASSET=1` when ES/NQ/ZN feeds exist |
| Rithmic trial NPZ | Trade-only from fixture bridge; full MBO requires R\|API |
| `REALIZED_VOL_STATE` (26) | Rolling std of tick-normalized mid returns in 1s window (Python + C++) |
| CHI404 live validate | Re-run on server after path/gate fixes |

## Verification

```bash
python -m pytest tests/ -q
graphify update .
```

See [GETTING_STARTED.md](GETTING_STARTED.md) for full operational flow.
