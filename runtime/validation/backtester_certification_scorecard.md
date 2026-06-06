# Backtester Certification Scorecard

- **Status:** YELLOW
- **Run ID:** CERT-20260606T055747Z-cb850fd9
- **Git SHA:** f053cfc2b50db32c6700efe81709e3804255f4ae
- **Timestamp UTC:** 2026-06-06T05:58:49.197198+00:00
- **Backtester Version:** f053cfc2-dirty

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
- Passed: False
- Returncode: 2
- Test paths: tests/test_options_lane
- Failure notes: ['TEST_PATH_MISSING: tests/test_options_lane']

### cme_futures
- Passed: True
- Returncode: 0
- Test paths: tests/backtester_validation/fast, tests/backtester_validation/full

## Warnings
- lane 'options' certification evidence unavailable (TEST_PATH_MISSING): TEST_PATH_MISSING: tests/test_options_lane

## Coverage
- Modules: ['backtest_pipeline', 'crypto_lane', 'equities_lane', 'execution', 'features_engine', 'options_lane', 'replay', 'workbench']
- Symbols: ['CL', 'ES', 'GC', 'HG', 'MES', 'MNQ', 'NQ', 'RTY', 'RUNNER', 'SI', 'YM', 'ZB', 'ZN']
- Event types: ['crypto_l2', 'crypto_l3', 'crypto_shock_event', 'equities_low_float', 'equities_runner_event', 'macro', 'options_parity']
- Latency bands (ms): [0.5, 1.0, 2.0, 5.0, 10.0, 50.0, 200.0]
- Queue models: ['LogProbQueueModel2', 'SquareProbQueueModel']
- Execution modes: ['REPLAY']
