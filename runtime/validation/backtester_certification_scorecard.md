# Backtester Certification Scorecard

- **Status:** GREEN
- **Run ID:** CERT-20260612T181144Z-0dc86bf9
- **Git SHA:** 66a734256e5db117d65d6a0457aea1e34db512a7
- **Timestamp UTC:** 2026-06-12T18:12:17.901553+00:00
- **Backtester Version:** 66a7342-dirty

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
