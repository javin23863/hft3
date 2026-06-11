# ALPHA_CRYPTO.md — Crypto Live-Alpha Campaign Milestones

Version: 2026-06-10.

**TIME-BOUND CAMPAIGN DOCUMENT — NOT A DURABLE CONTRACT.**
This document expires when C12 (live arm) is reached and is superseded by the
post-launch operations record. Milestone definitions, scope, and kill criteria
may be revised only by an explicit versioned edit to this file before C12.

Milestone IDs use prefix **C** (crypto). Not to be confused with the C-lane
CI column in CORRECTNESS.md §4 (per-C++-commit trigger); the crypto CI trigger
is the K-lane (CRYPTO_LIVE.md §10).

---

## 1. Scope

Tracks the engineering, measurement, and research milestones required to arm a
live Bitfinex BTC strategy on the Contabo BTC-node VPS (CRYPTO_LIVE.md §2,
decisions D1–D3). Governing specs are cited per row; this document adds no new
behavioral contracts — it references and sequences existing ones.

---

## 2. Milestone Register

| ID | Milestone | Machine | Governing spec | Status | Depends |
|----|-----------|---------|---------------|--------|---------|
| C0 | **Real venue RTT (provisional)** — add `bitfinex` to the venue URL map in `packages/crypto_lane/src/align/latency_profile.py`, then run `python -m crypto_lane.pipeline measure-live-ws-rtt --venue <v>` for `bitfinex`, `binance_perp`, `binance_spot`, `kraken_spot`, `kraken_futures`. Gate: no `synthetic_calibrated:*` source remains in `venue_profiles.json` for any promotion-relevant venue; K7 resolver hard-fail test created. Clears defect kc. Authoritative re-measurement from the live host folds into C9. | Workstation (provisional) | LATENCY.md §3, §10; CRYPTO_LIVE.md §8 K7 | **OPEN** | — |
| C1 | **Bitfinex L3 MBO production capture** — `python scripts/download_crypto_mbo.py --duration 3600` (repeat sessions; `--merge-all` accumulates) → `data/replay/hftbacktest/crypto/bitfinex/BTC_USD/BTC_USD_mbo.npz` with `execution_classification: L3_VALIDATED` sidecar. Gate: `python scripts/verify_crypto_replay_data.py` exit 0 on real (non-fixture) capture. | Workstation | CRYPTO_LIVE.md §6; `packages/crypto_lane/docs/CRYPTO_REPLAY_DATA.md` | **OPEN** | — |
| C2 | **Execution replay gate GREEN on real MBO** — `validate_crypto_candidate` routes BTCUSDT to `L3FifoQueueModel` on the real NPZ. Gate: `python -m crypto_lane.pipeline validate <candidate>` returns `L3_VALIDATED`; `python -m pytest tests/test_crypto_l2/test_crypto_execution_validator.py -q` green. | Workstation | CRYPTO_LIVE.md §6, K6 | **OPEN** | C1 |
| C3 | **Slippage calibration** — fit queue-model parameters to captured MBO; write `runtime/crypto_latency/slippage_calibration.json`; create freshness test. Gate: `python -m pytest tests/test_crypto_lane/test_slippage_calibration.py -q` green. Clears kf (provisional; re-fit against micro-live fills after C8). | Workstation | CRYPTO_LIVE.md §6.3, K12 | **OPEN** | C1 |
| C4 | **Live L2 wiring** — production ingest sets `l2_data_quality_flag=1` from a real recorded L2 source; L2-derived features non-null in the production feature matrix. Gate: K3 suite green + production-pull spot check showing flag=1 rows. Clears kd. | Workstation | CRYPTO_LIVE.md §8 K3; HANDOFF_2026_06_02.md gap 2 | **OPEN** | — |
| C5 | **Silent-zero closure** — internal quality awareness in `build_basis_features` / `build_deribit_vol_features` for standalone callers (flag or raise; never unflagged zeros). Gate: `python -m pytest tests/test_crypto_lane/test_feature_builders_silent_zero.py -q` green. Clears ke. | Workstation | CRYPTO_LIVE.md §8 K4 | **OPEN** | — |
| C6 | **CryptoExecutionAdapter build** — `CryptoPaperBrokerAdapter` + `CryptoLiveBrokerAdapter` (Bitfinex) in `packages/execution/adapters/`, factory wiring, safety counters, mode guards, venue-API safety semantics (cid idempotency, cancel-on-disconnect, rate-limit fail-closed, reconnect reconciliation). Gate: K1 + K2 suites green. Clears ka. | Workstation | CRYPTO_LIVE.md §3 | **OPEN** | — |
| C7 | **Risk + kill-switch wiring** — risk-check result enforced at the crypto call site; `LIVE_KILL_SWITCH` → cancel-all + halt ≤ 1 s; drill scripted. Gate: `python scripts/crypto_kill_drill.py --venue bitfinex_paper` exit 0. Clears kb. | Workstation → Contabo | CRYPTO_LIVE.md §4, K8 | **OPEN** | C6 |
| C8 | **Paper harness on Bitfinex paper sub-account** — adapter drives the paper venue end-to-end from the live host; first 100 real submit→ack pairs recorded per the LATENCY.md §9.2-analog protocol; zero synthetic timestamps. Micro-live risk sign-off recorded before any micro-live order (CRYPTO_LIVE.md §5.4). | Contabo | CRYPTO_LIVE.md §5; LATENCY.md §10 | **OPEN** | C6, C7 |
| C9 | **≥1000-paired-sample order-ack campaign + authoritative RTT** — `paper_order_latency.measured = true` and `order_ack_p99_ms` populated in `runtime/crypto_latency/latency_summary.json`; venue RTT re-measured from the live host (supersedes C0 provisional values). Clears kg together with C0. | Contabo | LATENCY.md §10 | **OPEN** | C8 |
| C10 | **Sweep at measured p99 + calibrated slippage** — existing walk-forward / deflated-Sharpe / BH / holdout pipeline (CRYPTO_H1–H7, `python -m crypto_lane.pipeline smoke`) re-run at the C9-measured order-ack p99 with the C3-calibrated queue model. Gate: survivors list non-empty; holdout kill-gate passes; micro-live fills (C8) within the calibrated envelope, else kf reopens. | Workstation | PIPELINE.md §4–5; LATENCY.md §3; CRYPTO_LIVE.md §6 | **OPEN** | C2, C3, C9 |
| C11 | **Bundle + paper-shadow** — first survivor → GREEN `promotion_eligible` stamp → bundle (DEPLOYMENT.md §1 schema, crypto deltas per CRYPTO_LIVE.md §7) → deployed to Contabo (`releases/<run_id>/` + `current` symlink, startup validation) → PAPER shadow over the embargo window (§4). Gate: DEPLOYMENT.md §4.4 criteria — positive net expectancy, zero code-attributable safety halts, determinism spot-check, fill/slippage within ±2σ calibrated envelope. | Both | CRYPTO_LIVE.md §7; DEPLOYMENT.md §4 | **OPEN** | C4, C5, C10 |
| C12 | **Live arm** — pre-arm checklist (DEPLOYMENT.md §5.1 pattern), `LIVE_*` values set per operator risk policy (decision D4), kill-switch drill re-run on a Bitfinex paper session, ARM entry appended to `runtime/validation/crypto_deployment_audit.jsonl`. Crypto defect ledger (CRYPTO_LIVE.md §9) MUST be empty. | Contabo | CRYPTO_LIVE.md §4, §9; DEPLOYMENT.md §5 | **OPEN** | C11, empty ledger |

---

## 3. Parallel-Chain Execution

Three chains run concurrently; they join at C10/C11:

```
Data chain:         C0 ──→ C1 → C2 → C3 ──────────────────────┐
                                                               ↓
Feature chain:      C4, C5 (independent) ───────────────→ C11 ← C10 → C12
                                                               ↑
Engineering chain:  C6 → C7 → C8 → C9 ─────────────────────────┘
```

C0 and C1 must start **immediately** — recording wall-clock time cannot be
recovered (same logic as ALPHA_CME.md M4/M5: capture/measurement time is the
non-compressible resource). C4 and C5 are independent of everything else and
can fill any idle capacity. The engineering chain C6–C9 is the critical path
to measured latency, which gates the C10 sweep.

---

## 4. 2026 Data Embargo (Binding)

The crypto walk-forward period table is identical to CME's (Discovery
2018–2020, Confirmation 2021–2022, Holdout 2023–2024, Recent holdout 2025 —
PIPELINE.md §5), so the embargo carries **verbatim** from DEPLOYMENT.md §4.2
and ALPHA_CME.md §4:

**Research sweeps, fitting, selection, and hyperparameter search must NEVER
read 2026 data. Promotion happens blind to 2026. The first touch of any 2026
market data is the deployed bundle running in REPLAY or PAPER mode on the
crypto live host (C11).**

Violation consequences:

- Shadow results for the affected bundle are **invalid**.
- A mandatory `DEFECT` entry must be appended to
  `runtime/validation/defect_ledger.jsonl` (append-only).
- C11 cannot pass for that bundle; a new bundle without the leak must be
  promoted.

Clarification for crypto-specific data: the embargo governs **market data used
for fitting/selection** (exchange ticks, klines, funding, vol surface, MBO,
mempool-derived features). Live operational artifacts produced by the
campaign itself (RTT profiles, order-ack samples, calibration fills) are not
research inputs and are exempt — they parameterize the simulator, they do not
train or select models.

---

## 5. Kill Criteria

Any of the following halts the campaign at the current milestone and requires
root-cause resolution before proceeding:

1. **Net expectancy ≤ 0** in any walk-forward OOS period during the C10 sweep.
   Primary kill gate — a hypothesis that cannot clear positive expectancy in
   any OOS window is not viable.
2. **Determinism failure** at C11: two REPLAY runs on identical inputs produce
   non-identical decision logs.
3. **Crypto defect ledger non-empty** at C12: any OPEN item in
   CRYPTO_LIVE.md §9 blocks live arm.
4. **2026 embargo violation** during C10: shadow results invalid; campaign
   stalls at C11 until a clean bundle is produced.
5. **Shadow acceptance criteria failure** at C11 (DEPLOYMENT.md §4.4):
   negative expectancy, code-attributable safety halts, or fill/slippage
   outside the calibrated envelope.
6. **Calibration invalidation** (crypto-specific): paper/micro-live fill or
   slippage observations outside the ±2σ calibrated envelope at C8–C11 reopen
   defect kf; the campaign stalls until the queue model is re-fit and the
   affected sweep results re-run.
