# Low-Latency Execution Path Audit Plan

## Objective

Perform a repo-level low-latency execution-path audit and add a permanent verification harness that proves which low-latency optimizations are actually active in the current trading path.

The audit must work inside the existing HFT3 Workbench, robustness, model metrics, and latency path. It must not create a disconnected latency side project, and it must not accept documentation or config as proof that an optimization is active.

## Core Requirement

Add a harness, centered around `apps/workbench/src/latency/execution_path_audit.py`, that traces the active path:

```text
market data received
-> decode
-> feature generation
-> model decision
-> defensive/hybrid arbitration when active
-> risk check
-> order construction
-> order send call
-> acknowledgment received asynchronously
```

The harness must report internal placement speed separately from external confirmation latency:

- Internal placement speed: market event to order/cancel/replace sent.
- External confirmation speed: order/cancel/replace sent to acknowledgment.

Acknowledgment latency must never be treated as placement speed.

## Optimization Categories To Verify

The audit must verify runtime evidence for:

- Critical language path: Python/C++/Rust participation, FFI or IPC boundaries, serialization cost, and whether Python is in the critical send path.
- Kernel bypass and network path: DPDK, Solarflare/OpenOnload, VMA, AF_XDP, NIC driver, interface, hugepages, userspace binding, and packet path classification.
- CPU pinning: process and critical thread affinity plus thread migration.
- NUMA locality: NIC node, critical CPU node, memory locality, and mismatch risk.
- Locking and contention: mutexes, queues, waits, sleeps, synchronous logging, persistence, and p99 wait time.
- Memory allocation: allocations during market-event handling, features, decision, risk, order construction, and before send.
- Logging and persistence: no file writes, JSON serialization, database writes, fsync, or blocking logs before `order_send_ts`.
- Serialization and copy cost: decode, feature object construction, model input serialization, order message construction, API send serialization, and IPC serialization.
- Risk path: risk latency, rules evaluated, blocking calls, and cache/database/network dependencies.
- Timestamp probes: market event, decode, features, decision, arbitration, risk, order build, send call/return, ack, cancel, and replace probes using monotonic timestamps.

## Required Outputs

Each audit run writes:

- `data/latency_audit/YYYY-MM-DD/<run_id>/spans.jsonl`
- `data/latency_audit/YYYY-MM-DD/<run_id>/runtime_env.json`
- `reports/latency_audit/<run_id>_summary.json`
- `reports/latency_audit/<run_id>_summary.md`
- `reports/latency_audit/current_low_latency_status.json`

`spans.jsonl` records per-event/action timing, including placement and acknowledgment metrics, per-stage timings, serialization cost, and raw monotonic timestamps.

`runtime_env.json` records host, kernel, CPU, NUMA, NIC, IRQ affinity, CPU affinity, kernel bypass status, coalescing settings, hugepages, and low-latency mode.

The summary must include an optimization status matrix with `active_verified`, `configured_not_active`, `missing`, `failed`, `needs_work`, or `unknown` statuses as applicable.

## Benchmark Modes

Add commands shaped like:

```powershell
python -m apps.workbench.src.latency.execution_path_audit --mode synthetic --duration 30
python -m apps.workbench.src.latency.execution_path_audit --mode replay --symbol ES --duration 120
python -m apps.workbench.src.latency.execution_path_audit --mode paper-live --broker rithmic --symbol ES --exchange CME --duration 300
```

Synthetic mode must run without broker connectivity. Replay mode must use deterministic market events. Paper-live mode must use real-time paper market data when available.

## Report Requirements

The Markdown report must answer:

- Current internal placement speed: p50, p90, p99, and p99.9 `tick_to_send_us`.
- Where time is spent: decode, features, model, arbitration, risk, order build, serialization, and send call.
- Which low-latency optimizations are active, inactive, missing, or failed.
- Largest bottleneck and next best optimization target.
- Whether the path is in a microsecond, sub-millisecond, or millisecond loop.
- Whether offensive, defensive, or hybrid model feasibility changes inside robustness testing.

## Robustness Pipeline Integration

After the harness works, connect its output to the existing robustness pipeline. Each model or composition leaving robustness must carry:

- `tick_to_send_us` bounds.
- `decision_to_send_us` bounds.
- cancel/replace send bounds when available.
- `send_to_ack_us` bounds.
- operating band.
- active optimization status.
- bottleneck classification.
- latency feasibility status.

Promotion must fail or warn when:

- `tick_to_send_us` p99 exceeds the configured budget.
- p99.9 regresses materially versus baseline.
- hot-path logging or allocation is detected.
- critical path unexpectedly falls back to Python.
- acknowledgment latency is used as placement speed.
- required low-latency mode is configured but not active.

## Default Thresholds

- `tick_to_send_us` p50 warning: greater than 100 us.
- `tick_to_send_us` p99 warning: greater than 500 us.
- `tick_to_send_us` p99.9 warning: greater than 1,000 us.
- hard fail if p99.9 worsens by more than 25% versus current baseline.
- hard fail if synchronous persistence occurs before `order_send_ts`.
- hard fail if blocking I/O occurs in the hot path.
- hard fail if Python appears in the low-latency send path unless explicitly allowed.

## Acceptance Criteria

- A single command runs the low-latency audit.
- The audit proves which optimizations are active at runtime.
- The audit decomposes market event to order send into stage timings.
- Placement speed and acknowledgment latency are reported separately.
- JSON, Markdown, and JSONL span outputs are written.
- The robustness pipeline consumes the result.
- Models leaving robustness carry latency capability and optimization-status metadata.
- Promotion gating can fail or warn based on latency feasibility.
- Synthetic mode passes without broker connectivity.

## Non-Goals

- Do not redesign the trading system.
- Do not optimize before measuring.
- Do not replace the existing robustness pipeline.
- Do not build a new model framework.
- Do not require live-money trading.
- Do not treat broker acknowledgment latency as placement speed.
- Do not accept documentation as proof that an optimization is active.
