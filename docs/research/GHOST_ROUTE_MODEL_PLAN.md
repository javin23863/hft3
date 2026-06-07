# Project Ghost Route: Isolated MBO Queue-Decay Alpha Model

Status: planning directive

## Objective

Build and test a single isolated predictive model named `ghost_route` inside
the Golden Pipeline.

Hypothesis:

> Level 3 MBO queue decay in macro CME futures contracts, ES, NQ, and YM, can
> anticipate stale quotes in the corresponding micro contracts, MES, MNQ, and
> MYM, before the micro book fully reprices.

This is a research/backtest model first. Do not rewrite the execution stack,
risk layer, data lake, or Golden Pipeline architecture. Implement this as a
standalone model module that consumes historical CME MDP 3.0 Level 3 MBO data
and emits structured signal objects for backtest simulation.

No external routing is authorized in this task. Output is a latency-adjusted
research report proving whether the edge survives realistic execution
assumptions.

## Contract Mapping

Supported macro-to-micro pairs:

- `ES -> MES`
- `NQ -> MNQ`
- `YM -> MYM`

For each pair, `X` is the macro lead contract and `Y` is the micro lag contract.

## Required Data Inputs

Use historical CME MDP 3.0 Level 3 Market-By-Order data where available.

Required fields:

- `exchange_timestamp`
- `local_receive_timestamp`
- `sequence_number`
- `instrument`
- `order_id`
- `event_type`
- `side`
- `price`
- `size`
- `remaining_size`
- `best_bid`
- `best_ask`
- `best_bid_size`
- `best_ask_size`
- `trade_price`
- `trade_size`
- `aggressor_side`

The backtester must preserve strict event ordering using exchange sequence
number first, then exchange timestamp, then local receive timestamp.

Reject or quarantine data windows with missing sequence numbers, non-monotonic
timestamps, crossed books, locked books unless explicitly allowed by config,
feed gaps, rollover ambiguity, or session boundary corruption.

## Configuration

Required `ghost_route` parameters:

```yaml
ghost_route:
  latency_wire_to_wire_us: 23
  compute_latency_us: measured_from_benchmark
  delta_t_us: configurable
  min_depth_contracts: configurable
  max_spread_ticks: configurable
  tau_decay_norm: configurable
  tau_remaining: configurable
  epsilon_trade: configurable
  tau_cancel_trade_ratio: configurable
  tau_ofi_norm: configurable
  tau_z: configurable
  min_expected_edge_ticks: configurable
  toxicity_enter_threshold: configurable
  toxicity_exit_threshold: configurable
  toxicity_min_hold_us: configurable
```

## Core Model Logic

### MBO Queue-Decay Detector

For macro contract `X`, track top-of-book Level 3 MBO behavior by side:

- cancel volume at the macro touch
- modify-down volume at the macro touch
- add volume at the macro touch
- executed trade volume at the macro touch
- visible top-of-book quantity

Raw queue decay:

```text
QD_s^X(t) = C_s^X(t) + D_s^X(t) - A_s^X(t)
```

Execution-adjusted shadow decay:

```text
SD_s^X(t) = QD_s^X(t) - T_s^X(t)
```

Normalized shadow decay:

```text
NSD_s^X(t) = SD_s^X(t) / max(Q_s^X(t - delta_t), epsilon_Q)
```

Remaining queue ratio:

```text
R_s^X(t) = Q_s^X(t) / max(Q_s^X(t - delta_t), epsilon_Q)
```

Cancel-to-trade ratio:

```text
CTR_s^X(t) = (C_s^X(t) + D_s^X(t)) / max(T_s^X(t), epsilon_T)
```

A valid shadow-decay event exists when:

```text
NSD_s^X(t) >= tau_decay_norm
AND T_s^X(t) <= epsilon_trade
AND R_s^X(t) <= tau_remaining
AND CTR_s^X(t) >= tau_cancel_trade_ratio
```

Bid-side macro shadow decay means downward pressure and a potential short signal
in micro `Y`. Ask-side macro shadow decay means upward pressure and a potential
long signal in micro `Y`.

The trigger must be based on MBO order cancellation/modification behavior, not
only top-of-book price movement.

### OFI Confirmation Layer

Compute top-of-book order-flow imbalance for macro `X`:

```text
OFI_t =
    I(P_b,t >= P_b,t-1) * V_b,t
  - I(P_b,t <= P_b,t-1) * V_b,t-1
  - I(P_a,t <= P_a,t-1) * V_a,t
  + I(P_a,t >= P_a,t-1) * V_a,t-1
```

Normalize:

```text
nOFI_t = OFI_t / max(V_b,t-1 + V_a,t-1, epsilon_Q)
```

Short confirmation requires bid shadow decay and `nOFI_t <= -tau_ofi_norm`.
Long confirmation requires ask shadow decay and `nOFI_t >= tau_ofi_norm`.

OFI is not allowed to trigger a trade by itself. It is only a confirmation layer
for MBO queue decay.

### Offline Lead-Lag Calibration

Do not compute expensive rolling cross-correlation in the hot path. Use offline
calibration only.

For each macro/micro pair, estimate lead-lag behavior using historical returns:

```text
r_X(t) = Mtilde_X(t) - Mtilde_X(t - delta_t)
r_Y(t) = Mtilde_Y(t) - Mtilde_Y(t - delta_t)
rho_XY(tau) = Cov(r_X(t), r_Y(t + tau)) / (sigma_X * sigma_Y)
tau_hat = argmax_tau |rho_XY(tau)|
```

Estimate spread model parameters using training data only:

```text
Mtilde_Y(t) = alpha + beta * Mtilde_X(t) + epsilon_t
```

Save per-pair calibration parameters:

```yaml
pair_calibration:
  ES_MES:
    alpha: ...
    beta: ...
    mu_spread: ...
    sigma_spread: ...
    tau_hat_us: ...
  NQ_MNQ:
    alpha: ...
    beta: ...
    mu_spread: ...
    sigma_spread: ...
    tau_hat_us: ...
  YM_MYM:
    alpha: ...
    beta: ...
    mu_spread: ...
    sigma_spread: ...
    tau_hat_us: ...
```

All calibration must be done with purged walk-forward splits and embargo. No
lookahead is allowed.

### Macro-To-Micro Stale Quote Detector

For pair `(X, Y)`, define normalized spread:

```text
S_XY(t) = Mtilde_Y(t) - alpha_XY - beta_XY * Mtilde_X(t)
Z_XY(t) = (S_XY(t) - mu_S,XY) / max(sigma_S,XY, epsilon_sigma)
```

Directional stale-quote logic:

- Short: micro `Y` is stale/rich when `Z_XY(t) >= tau_z`.
- Long: micro `Y` is stale/cheap when `Z_XY(t) <= -tau_z`.

Micro liquidity availability gate:

- Short uses `Q_bid_Y(t)` and targets `P_bid_Y(t)`.
- Long uses `Q_ask_Y(t)` and targets `P_ask_Y(t)`.

The stale quote condition is valid only when `abs(Z_XY(t)) >= tau_z`,
available depth is at least `min_depth_contracts`, and micro spread is no more
than `max_spread_ticks`.

### Expected Edge

Expected edge must be net of latency, fees, spread crossing, slippage, partial
fills, missed fills, and adverse selection. Gross edge cannot be reported as
success.

The model may only trigger when `ExpectedEdge_t >= min_expected_edge_ticks`.

### Final Ghost Route Trade Gate

A valid signal exists only when all conditions are true:

- shadow decay event
- OFI confirmation
- stale quote condition
- net expected edge threshold
- available depth threshold
- `global_risk_block == false`
- `data_quality_ok == true`

If the gate passes, emit a structured order-intent object for the backtester
with model id, signal timestamp, macro/micro contracts, direction, target price,
quantity, order type `FAK_LIMIT`, and a reason payload containing shadow decay,
`nOFI`, spread z-score, expected edge, available depth, and toxicity state.

## FAK Order Simulation

Simulate aggressive fill-and-kill limit orders against the micro contract `Y`.

Order arrival time:

```text
t_order_arrival = t_signal + compute_latency_us + latency_wire_to_wire_us
```

Default wire-to-wire latency is `23us`.

The simulator must check the reconstructed micro book at `t_order_arrival`.

Fill classifications:

- `FULL_FILL`
- `PARTIAL_FILL`
- `MISS_STALE_QUOTE_GONE`
- `MISS_REPRICED_BEFORE_ARRIVAL`
- `MISS_INSUFFICIENT_DEPTH`
- `REJECT_DATA_QUALITY`
- `REJECT_RISK_BLOCK`

Primary success requires arrival before micro reprice, filled quantity greater
than zero, and net PnL after costs greater than zero.

## Markout And Adverse Selection

For every fill, compute gross and net markouts after:

- `10us`
- `25us`
- `50us`
- `100us`
- `250us`
- `1ms`
- `5ms`

For direction `d`:

```text
Markout_h = d * (Mtilde_Y(t_fill + h) - FillPriceTicks)
AdverseSelectionPenalty_h = -min(Markout_h, 0)
```

## Flow Toxicity State Feed

Publish a toxicity state to the existing risk layer. Do not create a new risk
manager.

Inputs include aggressive volume imbalance, trade intensity z-score, cancel
intensity z-score, and book-thinning z-score. The composite toxicity score uses
weights `w1..w4`.

State logic uses hysteresis:

- enter `TOXIC` when score is at least `toxicity_enter_threshold`
- hold for at least `toxicity_min_hold_us`
- exit to `ELEVATED` or `NORMAL` only after hold and lower score

The toxicity state may inform the existing risk layer, especially for suspending
passive maker orders. It should not automatically block Ghost Route unless the
global risk layer explicitly blocks it.

## Backtest Requirements

Replay historical MBO events in event time:

1. Macro MBO event arrives.
2. Ghost Route updates macro queue state.
3. Ghost Route detects or rejects shadow decay.
4. Ghost Route checks OFI confirmation.
5. Ghost Route checks micro stale quote condition.
6. Ghost Route computes expected edge.
7. If all gates pass, Ghost Route emits order intent.
8. Backtester adds compute latency.
9. Backtester adds 23us wire-to-wire latency.
10. Backtester checks reconstructed micro book at order arrival.
11. Backtester simulates FAK fill, partial fill, or miss.
12. Backtester computes markouts and net PnL.

The simulator must include latency, fees, spread crossing, partial fills, missed
fills, slippage, adverse selection, session boundaries, contract rollovers,
event-time ordering, feed gaps, and data-quality rejection.

## Required Report

Generate report metrics covering:

- macro queue-decay events
- OFI-confirmed events
- stale-quote events
- final Ghost Route signals
- fill/miss rates
- stale-quote survival rate
- signal lead time before micro reprice
- expected edge before and after costs
- net expectancy per signal and per fill
- gross/net PnL
- fees, slippage, adverse selection
- false positives and false-negative estimate where measurable
- performance by pair, session, volatility regime, scheduled macro events,
  open/close, drawdown, tail loss, and worst 1% outcomes

Latency sensitivity must include `10us`, `23us`, `50us`, `100us`, `250us`, and
`1ms`. A strategy that works only at `0us` but fails at `23us` is not viable.

## Robustness Controls

Required controls:

- purged walk-forward validation
- embargo between train/test windows
- no lookahead
- no future book state during signal creation
- sequence-number validation
- timestamp monotonicity validation
- feed-gap detection
- rollover handling
- session segmentation
- fee inclusion
- latency inclusion
- partial-fill simulation
- missed-fill simulation
- adverse-selection markouts
- parameter sensitivity
- contract-pair sensitivity
- regime sensitivity

Thresholds must not be selected using the final test period.

## Deliverables

Create:

- `models/ghost_route/ghost_route_model.py`
- `models/ghost_route/ghost_route_config.yaml`
- `models/ghost_route/ghost_route_backtest.py`
- `models/ghost_route/ghost_route_metrics.py`
- `models/ghost_route/ghost_route_event_log_schema.json`
- `reports/ghost_route_backtest_report.md`
- `reports/ghost_route_backtest_report.csv`
- `tests/test_ghost_route_queue_decay.py`
- `tests/test_ghost_route_ofi.py`
- `tests/test_ghost_route_stale_quote.py`
- `tests/test_ghost_route_latency_sim.py`

The event log must include one row per triggered signal with signal id, signal
and arrival timestamps, macro/micro contracts, direction, macro and micro top of
book state, shadow decay side, `NSD`, `CTR`, `nOFI`, spread z-score, expected
edge, target price/quantity, available depth at signal and arrival, fill status,
filled quantity, fill price, micro reprice time, lead time, markouts, net PnL,
and reject reason.

## Acceptance Standard

Do not mark Ghost Route as viable unless it demonstrates positive expectancy
after all of:

- 23us wire-to-wire latency
- measured compute latency
- fees
- spread crossing
- partial fills
- missed fills
- slippage
- adverse selection
- realistic FAK simulation
- purged walk-forward validation

The model fails if edge exists only before latency, only before fees, depends on
lookahead, disappears under 23us latency, disappears under realistic FAK fills,
is concentrated in one tiny overfit window, has false positives dominate fills,
or has adverse selection overwhelm gross edge.

The final report must classify Ghost Route as one of:

- `PASS`: latency-adjusted, cost-adjusted edge survives
- `WATCHLIST`: possible edge but insufficient robustness
- `FAIL`: no tradable edge after realistic simulation

No subjective claims. No "it looks promising" without metrics.
