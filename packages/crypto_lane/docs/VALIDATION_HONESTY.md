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
4. **Mempool / btc-node** — `sync-node-host` / orchestrator pull chi404 btc-node status, `.btc-node.env`, and `data/crypto/gold/bitcoind/mempool/*.jsonl` via SSH; preflight counts B2 parquet **or** local/chi404 jsonl days. CAE sibling status remains fallback.
5. **ML challengers** — `lightgbm` and `xgboost` are required in `requirements.txt`; `env-check` reports `challengers` import status. Walk-forward evaluates all YAML challengers; production `pass_fail` fails on `challenger_errors`.
6. **Mempool coverage** — `mempool_ready` requires 100% B2 days or ≥95% coverage (`MEMPOOL_MIN_COVERAGE_RATIO`); normalized CSV alone does not pass. Audit splits `crypto_l3_ready` vs `crypto_mempool_ready` and samples ≤31 B2 probe days for speed.
7. **Purged CV challengers** — `purged_cv_ic_challengers` mirrors walk-forward challenger list.

## Closed (do not re-report)

- **θ sign convention** — `tests/test_crypto_lane/test_theta_sign_convention.py` asserts `T_local_true = T_nominal − θ`.
- **Synthetic L3 in production** — `walk_forward_runner._assert_production_ready` rejects synthetic bookticker days.

Closed in code (do not re-report): PIT join runs before mempool/event features; normalize no longer nominal-pairs mempool to exchange bars.

Spec: [PIT_AVAILABILITY_BOUNDARY.md](PIT_AVAILABILITY_BOUNDARY.md)
