# Worker Isolation Specification

Each scenario in a worker receives a fresh:

- `BacktestAsset` / `HashMapMarketDepthBacktest`
- strategy, execution adapter, replay session
- order book, queue, account, order-ID space, fill ledger, lifecycle audit

Workers import HftBacktest once and reuse **immutable** caches only.

Process pool: `spawn` context. No shared mutable engine across candidates.

Recycle worker after `max_scenarios_per_worker` or memory threshold.
