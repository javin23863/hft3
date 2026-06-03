# Backtester Certification Scorecard

- **Status:** GREEN
- **Run ID:** CERT-20260603T142035Z-4fd9a16b
- **Git SHA:** 80a61840568ca69349587ca881135890fff4a739
- **Timestamp UTC:** 2026-06-03T14:21:14.917354+00:00
- **Backtester Version:** 80a6184-dirty

## T0 Fast Gate (CME core)
- Passed: True
- Test count: 19

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
- Test paths: tests/test_equities_lane

### options
- Passed: True
- Returncode: 0
- Test paths: tests/test_options_lane

### cme_futures
- Passed: True
- Returncode: 0
- Test paths: tests/backtester_validation/fast, tests/backtester_validation/full

## Coverage
- Modules: ['backtest_pipeline', 'crypto_lane', 'equities_lane', 'execution', 'features_engine', 'options_lane', 'replay', 'workbench']
- Symbols: ['BTC/USD', 'BTCUSDT', 'CL', 'ES', 'ETH/USD', 'ETHUSDT', 'GC', 'HG', 'MES', 'MNQ', 'NQ', 'RTY', 'RUNNER', 'SI', 'SOLUSDT', 'YM', 'ZB', 'ZN']
- Event types: ['crypto_l2', 'crypto_l3', 'crypto_shock_event', 'equities_low_float', 'equities_runner_event', 'macro', 'options_parity', 'synthetic']
- Latency bands (ms): [0.5, 1.0, 2.0, 5.0, 10.0, 50.0, 200.0]
- Queue models: ['LogProbQueueModel2', 'SquareProbQueueModel']
- Execution modes: ['REPLAY']
