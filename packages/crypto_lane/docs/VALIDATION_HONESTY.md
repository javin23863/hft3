# Crypto lane — validation addendum

Repo-wide: **[docs/VALIDATION_HONESTY.md](../../../docs/VALIDATION_HONESTY.md)**

**Scope-green:** `python -m pytest tests/test_crypto_lane/ -q`

## validation_mode

| Mode | Use |
|------|-----|
| `fixture` | CI / offline default (`packages/crypto_lane/fixtures`) |
| `production` | Requires `data/crypto/normalized/*.csv` from ingest |

## Probe honesty

| Source | Label |
|--------|-------|
| Measured ping/pong artifact | live measured RTT |
| `calibrate-ws-rtt` / `probe-ws-rtt` CLI | synthetic calibration (`source: synthetic_calibrated:*`) |
| YAML `ws_rtt_ms` fallback | synthetic replay calibration |

## Known gaps (open)

1. ~~**θ convention audit**~~ — **Closed.** NTP sign convention corrected: `T_local_true = T_nominal + θ`, `T_exch_true = T_exch - θ_exch`, `T_avail = T_node_obs - θ_node + δ_net + δ_proc`. BTC mediantime no longer used as θ_node (it is consensus lag, not clock drift).
2. **Live venue RTT** — default path is synthetic calibration until real WS trace stored in `venue_profiles.json`.

Closed in code (do not re-report): PIT join runs before mempool/event features; normalize no longer nominal-pairs mempool to exchange bars.

Spec: [PIT_AVAILABILITY_BOUNDARY.md](PIT_AVAILABILITY_BOUNDARY.md)
