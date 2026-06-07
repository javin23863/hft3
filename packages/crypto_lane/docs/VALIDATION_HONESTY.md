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

### B2 purge gates

| Field | Probe | Used for |
|-------|-------|----------|
| `purge_safe` | Full B2 probe on **all** synthetic days (`b2_synthetic`) | `fill-l3-gaps --replace-synthetic`, gap-fill orchestrator |
| `purge_safe_estimate` | Sampled probe (≤31 days, extrapolated) | Audit dashboards only — **not** destructive decisions |

`crypto_readiness.json` includes `audited_at`, `synthetic_days`, and both purge fields. Production pytest re-validates cache age (24h) and live `synthetic_days` before running smokes.

Dry-run: one warm `summarize_bookticker_range` scan when cache is hot; no cache clear at entry; Vision probes off; full synthetic B2 probe cached 24h in `runtime/data_audits/b2_synthetic_probe_cache.json` (invalidated on bookticker ingest/purge). Writes **`crypto_readiness_dry_run.json` only** — never overwrites pytest gate file `crypto_readiness.json`.

Orchestrator fail-fast: aborts on `pull_gold`, `mempool_not_ready`, `normalize`, and `fill_l3_gaps` abort unless `--continue-on-error`. **`--continue-on-error` never normalizes after L3 abort.** `pit_strict_blocked` forces `ready=false` without `--ws-rtt-ms`. Only `scripts/audit_crypto_readiness.py` (always refreshes B2 synthetic probe by default) and successful full `fill-test-gaps` runs write `crypto_readiness.json`. After **CAE Contabo bookticker → B2** backfill, run `python scripts/audit_crypto_readiness.py` or `fill-test-gaps --dry-run --refresh-b2-probe` — do not trust a cached `purge_safe` from dry-run alone.

## Production testing entrypoints

| Command | Purpose |
|---------|---------|
| `python -m crypto_lane.pipeline fill-test-gaps --dry-run` | Preflight L3/mempool + CAE backfill status without downloads |
| `python -m crypto_lane.pipeline fill-test-gaps --sync-chi404-node --ws-rtt-ms <ms>` | Full gap-fill orchestration for full-year production |
| `python scripts/audit_crypto_readiness.py` | Crypto-only audit; **exit 0** only when `crypto_ready` |
| `scripts/fill_crypto_test_gaps.ps1` | Windows wrapper for `fill-test-gaps` |
| `python -m crypto_lane.pipeline smoke --production` | Walk-forward smokes using `*_production.yaml` configs |

**Prerequisite for full 2024 L3:** CAE Contabo `futures_um_bookticker` backfill (Apr–Dec 2024) → B2 before `fill-l3-gaps --replace-synthetic`. Check status via `audit_crypto_readiness` → `cae_bookticker_backfill_status`.

### CAE operator steps (external to hft3)

On Contabo (`btc-node` SSH alias), from `crypto-alpha-engine`:

```bash
bash scripts/contabo_max_cpu.sh                    # deriv pool + tick supervisors
.venv/bin/python scripts/data_collection_status.py --json
# Target dataset: futures_um_bookticker, symbol BTCUSDT, months 2024-04..2024-12
```

After B2 upload completes, from hft3:

```bash
python scripts/audit_crypto_readiness.py           # days_until_purge_safe → 0
python -m crypto_lane.pipeline fill-test-gaps --sync-chi404-node --ws-rtt-ms <ms>
```

## Known gaps (open)

1. **Sub-second exchange book PIT** — hourly bookticker aggregation only; see [PIT_AVAILABILITY_BOUNDARY.md](PIT_AVAILABILITY_BOUNDARY.md) §6.
2. **Production venue RTT** — `pit_strict` backtests require `calibrate-ws-rtt --live-measured --ws-rtt-ms <ms>`; synthetic default is fixture/replay only.
3. **2024-04+ true L3 bookticker** — not on B2; Binance Vision monthly incomplete/missing. Run `fill-l3-gaps --dry-run` before any purge. CAE bookticker backfill → B2 is the production path (see `cae_bookticker_backfill_status` in audit).
4. **Mempool / btc-node** — `sync-node-host` / orchestrator pull chi404 btc-node status, `.btc-node.env`, and `data/crypto/gold/bitcoind/mempool/*.jsonl` via SSH; preflight counts B2 parquet **or** local/chi404 jsonl days. CAE sibling status remains fallback.
5. **ML challengers** — `lightgbm` and `xgboost` are required in `requirements.txt`; `env-check` reports `challengers` import status. Walk-forward evaluates all YAML challengers; production `pass_fail` fails on `challenger_errors`.
6. **Mempool coverage** — `mempool_ready` requires 100% B2 days or ≥95% coverage (`MEMPOOL_MIN_COVERAGE_RATIO`); normalized CSV alone does not pass. Audit splits `crypto_l3_ready` vs `crypto_mempool_ready` and samples ≤31 B2 probe days for speed.
7. **Purged CV challengers** — `purged_cv_ic_challengers` mirrors walk-forward challenger list.

## Closed (do not re-report)

- **θ sign convention** — `tests/test_crypto_lane/test_theta_sign_convention.py` asserts `T_local_true = T_nominal − θ`.
- **Synthetic L3 in production** — `walk_forward_runner._assert_production_ready` rejects synthetic bookticker days.

Closed in code (do not re-report): PIT join runs before mempool/event features; normalize no longer nominal-pairs mempool to exchange bars.

Spec: [PIT_AVAILABILITY_BOUNDARY.md](PIT_AVAILABILITY_BOUNDARY.md)
