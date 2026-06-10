# Backtester Certification Scorecard

- **Status:** GREEN
- **Run ID:** CERT-20260610T003646Z-5ea3fe09
- **Git SHA:** dd4459eebeade2f911b549c63f8f0cd10a4a3cc0
- **Timestamp UTC:** 2026-06-10T00:36:58.676803+00:00
- **Backtester Version:** dd4459e-dirty

## T0 Fast Gate (CME core)
- Passed: True
- Test count: 21

## T2 Full Suite (CME core, adversarial)
- Passed: True
- Test count: 9

## Lane-aware Certification (Phase 38+)
## Coverage
- Modules: ['backtest_pipeline', 'execution', 'replay', 'features_engine', 'workbench']
- Symbols: ['ES', 'MES']
- Event types: ['macro', 'synthetic']
- Latency bands (ms): [0.5, 1.0, 2.0, 5.0, 10.0]
- Queue models: ['LogProbQueueModel2', 'SquareProbQueueModel']
- Execution modes: ['REPLAY']
