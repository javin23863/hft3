# Lane split — 2026-06-12

hft3 is CME-only from `core/cme-only` onward. The crypto and equities lanes moved — nothing deleted — to:

| Lane | Repo | Seeded from | Extra branches |
|------|------|-------------|----------------|
| Crypto (binance/deribit/bitfinex/kraken/bitcoind, Rust edge daemon) | `javin23863/hft3-crypto-lane` | tag `pre-lane-split-20260612` | `spec/crypto-production-set` |
| Equities (low-float runners, IBKR Web API, OPRA equity chains) | `javin23863/hft3-equities-lane` | tag `pre-lane-split-20260612` | `eqopt/live-probe`, `stocks-lane-restored` |

Full lane history remains in THIS repo's git log (`git log pre-lane-split-20260612 -- packages/crypto_lane`). `packages/options_lane` (CME futures-options parity) stays in core, registered under the historically-named `Lane.EQUITIES` with `OPTIONS_`/`PARITY_` prefixes.

Lane DATA is unchanged: `C:\hft3-lake\{crypto,equities,options}` + B2 `Hft3repo/lake/...` keep syncing nightly (data ≠ code).

Per-lane onboarding files to copy into the lane repos: [`LANE_README_crypto.md`](LANE_README_crypto.md), [`LANE_README_equities.md`](LANE_README_equities.md).
