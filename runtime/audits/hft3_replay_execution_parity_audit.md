# HFT3 Replay Execution Parity Audit

Generated as Phase 1 of replay execution parity work. Answers the 15 mandated audit questions with repo evidence.

## Executive summary

Historical replay is **not** uniformly order-disabled. The repo runs **two research engines**:

1. **Primary (`event_accurate_mbo`)** — `SignalBacktester` scores hypotheses and simulates fills internally. No `OrderIntent`, no execution adapter, no HftBacktest orders.
2. **Secondary (`hftbacktest_loop`)** — `CombinedHypothesisStrategy` calls `hbt.submit_buy_order` / `submit_sell_order` directly, bypassing any OMS/adapter layer.

Parity requires a unified **mode-blind OrderIntent → ExecutionAdapter** path with HftBacktest as the REPLAY adapter backend, replacing SignalBacktester fill simulation.

## Audit answers

| # | Question | Answer |
|---|----------|--------|
| 1 | Does `CombinedHypothesisStrategy.on_step` submit during replay? | **Partial** — yes on HftBacktest path; no on primary SignalBacktester path |
| 2 | Where does the decision stop? | SignalBacktester: signal → `_PendingAction` → `_apply_pending`. Hft: direct `hbt.submit_*` |
| 3 | Scores vs OrderIntent? | Primary emits scores only; no `OrderIntent` type |
| 4 | Different path from external broker? | Yes — three parallel lanes (SignalBacktester, hbt, trial capture, C++ stub) |
| 5 | `if backtest: do_not_send`? | De facto via `--skip-hftbacktest` and `skip_hft=True` default; no literal branch in strategy |
| 6 | HftBacktest order methods? | Yes: `submit_buy_order`, `submit_sell_order`; PDF hybrid also `cancel` |
| 7 | Fills/cancels/rejects captured? | Engine-internal only; not lifecycle-audited |
| 8 | Inventory updated? | Via `hbt.position(0)` or `_HypSimState.position` |
| 9 | PnL updated? | Terminal `state_values(0)` or SignalBacktester `_finalize` |
| 10 | Lifecycle logged? | **No** JSONL audit |
| 11 | Latency on lifecycle? | `constant_order_latency` on asset; deferred fills in SignalBacktester |
| 12 | Queue models? | Yes — `LogProbQueueModel2`, `SquareProbQueueModel` in `ReplayRunner.build_backtest` |
| 13 | Compatible order objects? | **No** shared contract |
| 14 | External-broker risk in replay? | **No** today (no live adapter wired) |
| 15 | Smallest patch? | Adapter layer + ReplaySession + deprecate SignalBacktester fills (not a one-line fix) |

## Key evidence

- `backtest_pipeline/src/hft_strategy.py:118-129` — threshold gates → `_submit` → `hbt.submit_*`
- `backtest_pipeline/src/signal_backtester.py:205-226` — per-hypothesis signal → pending fill, no adapter
- `backtest_pipeline/src/runner.py:38-49` — latency bands + queue models on BacktestAsset
- `scripts/run_event_replay.py:106,269` — primary engine label + optional HftBacktest skip
- `docs/vault/RESEARCH_ENTRYPOINTS.md:19` — documents SignalBacktester as primary
- `backtest_pipeline/src/research_runner.py:24` — `skip_hft=True` default

## Remediation (implemented in follow-on phases)

1. `execution/` — `OrderIntent`, `OrderEvent`, `ExecutionAdapter`, factory, safety gates
2. `execution/adapters/hftbacktest_simulated_exchange.py` — wrap HftBacktest (no new simulator)
3. `replay/replay_session.py` — MBO → MarketState → strategy → adapter → lifecycle audit
4. Deprecate SignalBacktester fill path; migrate consumers to ReplaySession matrix
5. Refactor strategies to emit `OrderIntent` (mode-blind)
