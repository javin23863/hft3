# Backtester Certification Scorecard

- **Status:** GREEN
- **Run ID:** CERT-20260611T144327Z-4efd5650
- **Git SHA:** 8887a917b7062d7d745c25f08fd63b414174772a
- **Timestamp UTC:** 2026-06-11T14:46:35.662155+00:00
- **Backtester Version:** 8887a91-dirty

## T0 Fast Gate (CME core)
- Passed: True
- Test count: 21

## T2 Full Suite (CME core, adversarial)
- Passed: True
- Test count: 9

## Lane-aware Certification (Phase 38+)
### crypto
- Passed: True
- Returncode: 0
- Test paths: tests/test_crypto_lane

### equities
- Passed: True
- Returncode: 0
- Test paths: tests/test_equities_lane, tests/test_workbench/test_options_lane_campaign.py

### cme_futures
- Passed: True
- Returncode: 0
- Test paths: tests/backtester_validation/fast, tests/backtester_validation/full

## Coverage
- Modules: ['backtest_pipeline', 'crypto_lane', 'equities_lane', 'execution', 'features_engine', 'replay', 'workbench']
- Symbols: ['CL', 'ES', 'GC', 'HG', 'LOW_FLOAT', 'MES', 'MNQ', 'NQ', 'OPTIONS', 'PARITY', 'RTY', 'RUNNER', 'SI', 'YM', 'ZB', 'ZN']
- Event types: ['crypto_l2', 'crypto_l2_depth', 'crypto_l3', 'crypto_shock_event', 'equities_low_float', 'equities_runner_event', 'macro', 'options_parity', 'parity', 'synthetic']
- Latency bands (ms): [0.5, 1.0, 2.0, 5.0, 10.0, 50.0, 200.0]
- Queue models: ['LogProbQueueModel2', 'SquareProbQueueModel']
- Execution modes: ['REPLAY']
