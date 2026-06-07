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

1. **Sub-second exchange book PIT** — hourly bookticker aggregation only; see [PIT_AVAILABILITY_BOUNDARY.md](PIT_AVAILABILITY_BOUNDARY.md) §6.
2. **Production venue RTT** — `pit_strict` backtests require `calibrate-ws-rtt --live-measured --ws-rtt-ms <ms>`; synthetic default is fixture/replay only.
3. **2024-04+ true L3 bookticker** — not on B2; Binance Vision monthly incomplete/missing. Run `fill-l3-gaps --dry-run` before any purge. CAE bookticker backfill → B2 is the production path.

## Closed (do not re-report)

- **θ sign convention** — `tests/test_crypto_lane/test_theta_sign_convention.py` asserts `T_local_true = T_nominal − θ`.
- **Synthetic L3 in production** — `walk_forward_runner._assert_production_ready` rejects synthetic bookticker days.

Closed in code (do not re-report): PIT join runs before mempool/event features; normalize no longer nominal-pairs mempool to exchange bars.

Spec: [PIT_AVAILABILITY_BOUNDARY.md](PIT_AVAILABILITY_BOUNDARY.md)
