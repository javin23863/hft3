# Point-in-Time Availability Boundary (crypto lane)

Canonical math for blending exchange bars with local Bitcoin Core mempool snapshots without structural lookahead bias.

Implementation: `packages/crypto_lane/src/align/pit_join.py`, `clock_sync.py`, `latency_profile.py`.

## 0. Implementation status

| Layer | Status |
|-------|--------|
| **Spec (this doc)** | Target semantics — equations define required PIT behavior |
| **Code** | **Partial** — see [VALIDATION_HONESTY.md](../../../docs/VALIDATION_HONESTY.md) and crypto addendum [VALIDATION_HONESTY.md](VALIDATION_HONESTY.md) known gaps |
| **Verify gate** | `python -m pytest tests/test_crypto_lane/ -q` must be scope-green before claiming PIT-complete |

Do not cite this document alone as proof of completed implementation. Agents must report the repo-wide [VALIDATION_HONESTY.md](../../../docs/VALIDATION_HONESTY.md) status block on every handoff.

## 1. Clock drift estimation (PTP/NTP)

Four-timestamp handshake between local collector and remote clock:

```
RTT = (T4 - T1) - (T3 - T2)
θ   = ((T2 - T1) + (T3 - T4)) / 2       (NTP: θ = remote − local; θ > 0 → remote ahead)
T_local_true = T_local_nominal + θ      (add θ to convert local → true)
```

Code: `compute_rtt_ms`, `compute_theta_ms`, `node_offset_from_handshake` in [`clock_sync.py`](../src/align/clock_sync.py).

For exchange matching engines without published clock offset, hft3 supports **WebSocket ping/pong RTT measurement** when a real trace exists:

```
θ_exch ← exchange_offset_from_ws_rtt(ping_send, pong_recv, venue=...)
```

**Live measured** probes write `data/crypto/latency/venue_profiles.json`. **Synthetic replay calibration** uses `calibrate-ws-rtt` or YAML `ws_rtt_ms` when no live artifact exists. See crypto [VALIDATION_HONESTY.md](VALIDATION_HONESTY.md) probe table.

## 2. Availability Boundary Equation

A node observation at nominal time `T_node_obs` is unavailable until it traverses the network and ingest pipeline.

**True availability timestamp:**

```
T_avail = T_node_obs - θ_node + δ_net + δ_proc
```

**True exchange decision time:**

```
T_exch_true = T_exch - θ_exch
```

**Availability condition (required for feature inclusion):**

```
T_avail ≤ T_exch_true
```

Violation in `pit_strict` mode raises `StructuralDataLeakageError`.

**As-of join:** The backtest engine selects the latest node row with `T_avail ≤ T_exch_true` (not nominal `T_node_obs ≤ T_exch`). See `backward_join_node_to_exchange`.

One-way network latency from round-trip measurement:

```
δ_net = RTT / 2    (one_way_latency_ms)
```

## 3. Staleness weight W(Δ_stale)

```
Δ_stale = T_exch_true - T_avail
```

Stepwise viability for max staleness `Δ_max` (H4–H7: **15000 ms**):

```
W(Δ_stale) = 1  if 0 ≤ Δ_stale ≤ Δ_max
           = 0  if Δ_stale > Δ_max
```

When `W = 0` or `T_avail > T_exch_true`, `btc_node_data_available_flag = 0` and node feature columns are set to missing (no forward-fill).

## 4. Implementation map

| Component | File |
|-----------|------|
| PIT alignment + join | `src/align/pit_join.py` |
| RTT / θ estimation | `src/align/clock_sync.py` |
| Venue + node latency profiles | `src/align/latency_profile.py` |
| Mempool feature builder | `src/features/onchain/btc_node_mempool_features.py` |
| Feature matrix assembly | `src/features/feature_matrix.py` |
| Ingest latency columns | `src/ingest/mempool_pull.py`, `normalize.py` |

CLI: `python -m crypto_lane.pipeline calibrate-ws-rtt --venue binance_perp [--ws-rtt-ms 5] [--measure-node]` (`probe-ws-rtt` is deprecated alias)

## 5. θ_exch policy

1. **Live measured:** Real WebSocket ping/pong trace → `venue_profiles.json`
2. **Synthetic replay calibration:** Derive from `latency_assumptions.ws_rtt_ms` in backtest YAML when artifact absent (not a live probe)
3. **Never:** Silent `θ_exch = 0` in production node+exchange blends without explicit calibration

## 6. Non-goals

- Nominal-timestamp as-of join (forbidden)
- Forward-fill of stale or unavailable node metrics
- Sub-second exchange order-book PIT (current bronze is 1h klines; bar close lag audit remains a separate concern)
