# Latency-Aware Capability Modeling Plan

## Objective

Add latency-aware capability modeling to the system so offensive models, defensive models, and hybrid model configurations can use measured speed as an input during testing, simulation, arbitration, and execution research.

This should be placed in the appropriate component or components where the system needs to understand timing, order state, pending exposure, model interaction, and execution feasibility.

## Core Problem

Latency metrics should not be treated as passive report numbers. The system needs to understand what those numbers mean operationally.

The system must know:

- how fast it can detect a market event
- how fast it can produce a decision
- how fast it can launch an order
- how fast it can launch a cancel
- how fast it can launch a replace
- how long external acknowledgments take
- how much pending exposure exists while waiting for acknowledgments
- whether a model combination is mathematically feasible at the required speed
- whether other market participants are likely operating faster or slower
- whether the strategy can realistically achieve its intended outcome at the measured speed

## Core Principle

Do not build the system as:

```text
Send order -> wait for acknowledgment -> think again
```

Build it as:

```text
Send order -> mark local state as pending -> continue processing market data -> update state asynchronously when ack/fill/reject arrives
```

Acknowledgments are required for official state reconciliation, but they must not block the next decision cycle unless the specific test configuration intentionally requires blocking behavior.

## Latency Categories

Separate internal operating speed from external confirmation speed.

Internal operating speed:

- `tick_to_decision_us`
- `decision_to_send_us`
- `tick_to_send_us`
- `cancel_to_send_us`
- `replace_to_send_us`

External confirmation speed:

- `send_to_ack_us`
- `cancel_to_ack_us`
- `replace_to_ack_us`
- `fill_received_latency_us`, if available

Primary interpretation:

- Offensive model speed = how fast the system can identify and attack an opportunity
- Defensive model speed = how fast the system can cancel, replace, reduce exposure, or avoid adverse selection
- Hybrid model speed = how fast the tested combination of offensive and defensive models can coordinate, arbitrate, or sequence actions under different timing assumptions

## Model Interaction Testing

The system should not assume the correct relationship between offensive, defensive, and hybrid models. This is unknown and must be testable.

Implement configurable test modes for model interaction:

1. Offensive only: only offensive models generate actions.
2. Defensive always active: defensive models monitor every event and may override, cancel, replace, or block offensive actions.
3. Defensive pre-action only: defensive models evaluate before an offensive order is launched.
4. Defensive during-action: defensive models monitor while orders are pending and may trigger cancel/replace behavior.
5. Defensive post-action: defensive models evaluate after an order is sent and manage exposure, pending state, or exit logic.
6. Concurrent offensive/defensive: offensive and defensive models run at the same time and an arbitration process decides the final action.
7. Hybrid model configuration: hybrid models test different ways offensive and defensive logic may work together, including timing, weighting, gating, sequencing, override rules, and state-dependent activation.

These modes should be parameterized so they can be tested, compared, and ranked rather than hardcoded.

## State Model

When an order action is sent, immediately update local state:

- new order sent -> `PENDING_NEW`
- cancel sent -> `PENDING_CANCEL`
- replace sent -> `PENDING_REPLACE`

When external messages arrive, update state asynchronously:

- `PENDING_NEW` -> `ACKED` / `WORKING` / `REJECTED`
- `WORKING` -> `PARTIALLY_FILLED` / `FILLED`
- `PENDING_CANCEL` -> `CANCELED` / `CANCEL_REJECTED`
- `PENDING_REPLACE` -> `REPLACED` / `REPLACE_REJECTED`

The system must continue processing market data while these states are pending, subject to configured risk limits and the specific test mode being evaluated.

## Capability Modeling

After each latency baseline run, convert raw latency into operational capability.

The output should answer:

- Can the system operate in a microsecond internal loop?
- Can it attack before the expected opportunity decays?
- Can it cancel or replace fast enough to reduce adverse selection?
- Does acknowledgment delay create stale-state risk?
- How many actions can be safely pending at once?
- Does a given offensive/defensive/hybrid model configuration fit within the available latency budget?
- Is the desired outcome mathematically feasible given our speed and assumed competitor speeds?

## Operating Bands

Classify internal speed:

- Microsecond loop: `tick_to_send_us < 100 us`
- Sub-millisecond loop: `tick_to_send_us < 1,000 us`
- Millisecond loop: `tick_to_send_us >= 1,000 us`

Classify external acknowledgment separately:

- `send_to_ack_us`
- `cancel_to_ack_us`
- `replace_to_ack_us`

Do not classify acknowledgment latency as placement speed.

## Speed-Aware Testing

Add the ability for tests to use latency as an input variable.

The system should be able to test:

- our measured speed
- slower simulated speed
- faster simulated speed
- assumed competitor speed
- opportunity decay time
- queue-position assumptions
- pending-order limits
- cancel/replace timing
- acknowledgment delay
- defensive activation timing
- hybrid coordination timing

The goal is not only to know how fast we are, but to test what strategies remain viable at that speed.

## Required Outputs

For each latency baseline and model interaction test, generate a capability report containing:

1. Offensive capability
   - `tick_to_decision_us`
   - `decision_to_send_us`
   - `tick_to_send_us`
   - operating band
   - estimated opportunity window compatibility
2. Defensive capability
   - `cancel_to_send_us`
   - `replace_to_send_us`
   - `cancel_to_ack_us`
   - `replace_to_ack_us`
   - stale-state risk
3. Hybrid configuration capability
   - selected model interaction mode
   - arbitration/sequencing latency
   - total decision-to-action latency
   - pending exposure behavior
   - whether the configuration improved or degraded outcome quality
4. External confirmation behavior
   - `send_to_ack_us`
   - `cancel_to_ack_us`
   - `replace_to_ack_us`
   - acknowledgment lag classification
5. Feasibility statement
   - plain-language summary explaining what speed range the system is operating in
   - what model behaviors are feasible
   - what behaviors are too slow
   - what parts of the system are bottlenecks

## Risk Controls

Because the system continues operating before acknowledgments arrive, enforce:

- max pending orders
- max pending quantity
- max pending notional exposure
- duplicate order protection
- client order ID tracking
- stale pending-order timeout
- cancel/replace throttles
- reject handling
- state reconciliation
- kill switch

## Deliverable

Implement a latency-aware capability module that allows the system to understand its own speed, use that speed in offensive/defensive/hybrid model testing, simulate other participant speed assumptions, and determine whether a given model configuration is operationally feasible.

## Acceptance Criteria

The task is complete when:

- latency is separated into internal operating speed and external confirmation speed
- the system does not block on acknowledgment unless a test mode explicitly requires it
- offensive, defensive, and hybrid model configurations can be tested under different timing assumptions
- latency baselines are converted into operational capability statements
- model tests can use our speed, assumed competitor speed, opportunity decay, and pending-state risk as variables
- reports explain what the system can and cannot realistically do at the measured speed
- the implementation is placed in the appropriate system component without hardcoding assumptions about where final orchestration belongs

## Non-Goals

- Do not redesign the trading system.
- Do not assume the correct offensive/defensive/hybrid relationship.
- Do not hardcode one model interaction style.
- Do not treat acknowledgment latency as placement speed.
- Do not require live-money trading.
- Do not optimize latency yet; first make the system understand and test its current capability.
