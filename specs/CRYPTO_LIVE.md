# CRYPTO_LIVE.md — Crypto Venue Live Contract

Version: 2026-06-10. Companion to DEPLOYMENT.md (CHI404/CME authoritative),
LATENCY.md, CORRECTNESS.md, PIPELINE.md. Authoritative for crypto lane order
submission, risk, paper trading, deployment, and the crypto no-bugs regime,
regardless of host.

Decision record: vault `decisions/2026-06-10 Crypto production spec set.md`.
Ratified decisions bound into this document: **D1** live venue = Bitfinex;
**D2** live host = Contabo BTC-node VPS; **D3** paper strategy = hybrid
(Bitfinex paper sub-account + minimum-size micro-live envelope); **D4**
capital/risk values = operator policy, set at arm time (ALPHA_CRYPTO.md C12).

This document **inherits by reference and overrides explicitly**: bundle
manifest schema (DEPLOYMENT.md §1.2), startup validation pattern (§3),
rollback procedure (§6), and audit hash-chain record format (§7) are cited,
not restated. Only crypto deltas appear here.

---

## 1. Scope and Authority

Governs the crypto lane's path from GREEN certification stamp to an armed live
session on the crypto live host, plus the lane-scoped correctness regime (§8)
and defect ledger (§9).

Explicitly **not** redefined here:

- Research/validation machinery — walk-forward, deflated Sharpe,
  Benjamini-Hochberg, purged K-fold, holdout kill-gate
  (`packages/crypto_lane/src/ml/`) — governed by PIPELINE.md §4–5.
- Replay data classes and the L3 MBO gate — source of truth is
  `packages/crypto_lane/docs/CRYPTO_REPLAY_DATA.md`.
- PIT availability boundary math —
  `packages/crypto_lane/docs/PIT_AVAILABILITY_BOUNDARY.md`.
- Edge daemon build/deploy/ops —
  `packages/crypto_lane/edge_daemon/README.md` and
  `infrastructure/crypto_lane/` systemd units.
- CME lane anything — DEPLOYMENT.md, CHI404_RUNTIME.md, ALPHA_CME.md.

Campaign sequencing for this contract lives in ALPHA_CRYPTO.md (time-bound).

---

## 2. Topology (D2: Contabo BTC-node VPS)

The crypto lane live/paper host is the **Contabo VPS that runs Bitcoin Core
and the edge daemon**. Rationale (decision note, D2): the mempool feature path
(`T_avail` < 300 ms via edge daemon) already terminates there; co-location
with the feature source minimizes feature staleness for the [5, 50, 200] ms
crypto latency bands (LATENCY.md §3).

**Hard-rule amendment dependency.** AGENTS.md ("Topology: Chicago colo only")
and BLUEPRINT.md §4 previously bound ALL live/paper paths to CHI404. This
contract is valid only together with the lane-scoped amendment in those files:
CME live/paper on CHI404 only; **crypto live/paper on the Contabo BTC-node VPS
only**; the dev workstation remains offline research for every lane. Routing
crypto live/paper data or orders through the workstation or CHI404 is a
topology violation exactly as routing CME through the workstation is.

### 2.1 Host requirements

| Requirement | Contract |
|---|---|
| Clock sync | chrony/NTP active; node clock offset θ_node within the `MAX_CLOCK_DRIFT_MS = 5000.0` clamp (`packages/crypto_lane/src/align/latency_profile.py`) |
| Timestamping | All submit/ack and packet timestamps from monotonic clocks (`time.perf_counter_ns()` / `std::chrono::steady_clock`) — never wall-clock deltas |
| Edge daemon isolation | `btc-edge-daemon` and the crypto execution process run as **separate systemd units under separate non-root users** with `MemoryMax`/`CPUQuota` resource limits, so an execution-process fault cannot take down the mempool feed or vice versa (blast-radius control for co-locating both duties on one host) |
| Monitoring | Edge daemon Prometheus metrics scraped at `127.0.0.1:9090/metrics`; receiver status artifacts under `runtime/crypto_edge/` |
| Latency artifacts | `runtime/crypto_latency/latency_summary.json` (LATENCY.md §10) — separate file from the CME `runtime/latency_reports/latency_summary.json` |
| Secrets | Venue API keys via env / `.env` per `packages/crypto_lane/src/config/env_loader.py` conventions (redaction, no values in logs); never committed |
| Network | Outbound WS/REST to Bitfinex only from the live host; no listening ports added beyond existing edge daemon/metrics |

### 2.2 What stays where

| Host | Crypto lane role |
|---|---|
| **Contabo VPS** | Bitcoin Core + edge daemon (existing); crypto paper/live execution process; order-ack measurement campaign; live RTT authority |
| **Dev workstation** | Offline research, replay, pytest, MBO capture sessions, sweep, bundle build — never crypto paper/live order routing |
| **CHI404** | CME lane only; no crypto execution duty |

---

## 3. Execution Adapter Contract (D1: Bitfinex)

Current state (defect ka, §9): `packages/execution/` contains no crypto code;
`adapter_factory.create_adapter` wires only
`HftBacktestSimulatedExchangeAdapter` / `PaperBrokerAdapter` /
`LiveBrokerAdapter`.

Contract for the build (ALPHA_CRYPTO.md C6):

1. **Adapters.** `CryptoPaperBrokerAdapter` and `CryptoLiveBrokerAdapter` in
   `packages/execution/adapters/`, implementing
   `execution.interfaces.ExecutionAdapter`, targeting Bitfinex
   (WS `wss://api.bitfinex.com/ws/2` authenticated channel + REST v2).
   Created **only** via `adapter_factory.create_adapter` (mode-keyed;
   `safety.reset_counters()` on create), same as the existing three.
2. **Safety counters and mode guards** (`packages/execution/safety.py`):
   - new `crypto_order_call_count` + `record_crypto_order_call()`;
     included in `counter_snapshot()`.
   - `assert_replay_safe()` forbidden-name tuple extended with
     `"CryptoPaperBrokerAdapter"`, `"CryptoLiveBrokerAdapter"`.
   - `assert_paper_safe()` extended to forbid `CryptoLiveBrokerAdapter`
     in PAPER mode.
3. **Single submission gate.** Exactly one call site of the venue order
   submission function (the Bitfinex order-new wire call) across
   `packages/execution/adapters/crypto_*.py`, reachable only through
   startup-validated `assert_live_config()` (fail-closed) plus an enforced
   risk check (§4). Mirrors the CME single-gate decision
   (vault `decisions/2026-06-10 CHI404 production spec set.md`, decision 2).
   Enforcement: §8 row K1.
4. **Venue-API safety semantics** (no CME analog; binding):
   - **Client-order-ID idempotency**: every order carries a unique `cid`;
     on reconnect, the adapter reconciles by `cid` before any resubmission —
     a replayed snapshot must never cause a duplicate order.
   - **Cancel-on-disconnect**: enabled via the venue's dead-man's-switch
     mechanism where supported; where not, the adapter's own disconnect
     handler issues cancel-all on reconnect before re-arming.
   - **Rate-limit budgeter**: REST/WS request budgeter that fails CLOSED on
     HTTP 429 / venue rate-limit error — order flow halts rather than retries
     hot.
   - **Reconnect reconciliation**: after any WS drop, order state and position
     are reconciled against venue REST snapshots before the adapter accepts
     new order intents.

---

## 4. Risk and Kill-Switch Wiring

1. **Enforced risk check.** The risk check's return value is enforced **at the
   crypto submission call site**: BLOCK/HALT results close the submission gate
   (order-call counter must not advance). The CME ledger's defect (c)
   (CORRECTNESS.md §3 — `RiskManager::check_order` return silently dropped)
   must not be replicated on the crypto path. Enforcement: §8 row K1.
2. **LIVE_* env contract** reused verbatim from
   `packages/execution/safety.py` `assert_live_config()`:
   `LIVE_MAX_ORDER_SIZE`, `LIVE_DAILY_LOSS_LIMIT`, `LIVE_KILL_SWITCH`,
   `LIVE_RISK_ENABLED` — required in the live host environment; values are
   operator policy (D4) and must be set before arm (ALPHA_CRYPTO.md C12).
3. **Kill-switch drill.** Scripted, exit-code-gated drill
   (`scripts/crypto_kill_drill.py`, to be created at C7): fire the
   kill-switch signal during an open paper session → adapter submits
   cancel-all and the engine halts within **1 s**. Mirrors DEPLOYMENT.md §5.1
   kill-switch fire drill. Enforcement: §8 row K8. The drill is re-run as a
   pre-arm item at C12.
4. **Position reconciliation before re-arm.** After any restart, rollback, or
   reconnect, the adapter must complete position reconciliation against venue
   REST state before `armed` may be set true (analog of CHI404_RUNTIME.md §9
   final-reconcile; DEPLOYMENT.md §6.2 position-zero constraint applies to
   crypto rollback unchanged).

---

## 5. Paper Harness (D3: hybrid)

1. **Plumbing + order-ack campaign** run against a **Bitfinex paper
   sub-account** through `CryptoPaperBrokerAdapter` — real venue auth, real WS
   order channel, synthetic matching.
2. **Timestamp protocol** — verbatim analog of LATENCY.md §9.2: paired
   `submit_ns` (immediately before the venue order-new wire call) and `ack_ns`
   (on entry of the venue order-ack WS callback), both from
   `time.perf_counter_ns()`. Synthetic or derived timestamps are prohibited in
   paired records; any synthetic record must carry `shadow_synthetic: true`
   and never feed the authoritative summary.
3. **Sample gate** — `runtime/crypto_latency/latency_summary.json` may set
   `paper_order_latency.measured = true` only at **n ≥ 1000** paired samples
   collected on the live host (LATENCY.md §10).
4. **Micro-live envelope clause.** Paper-venue fills are synthetic; therefore
   **fill/slippage calibration data must come only from minimum-size real
   Bitfinex orders** (micro-live), never from paper fills. Micro-live sessions
   require `LIVE_*` config set, the §4 risk path active, and an explicit
   operator risk sign-off recorded as an audit-log entry (§7) **before** any
   micro-live order; total micro-live notional is capped by
   `LIVE_MAX_ORDER_SIZE` at minimum venue order size.
5. Paper-shadow protocol for promoted bundles: DEPLOYMENT.md §4 applies with
   the crypto deltas in §7 below.

---

## 6. Replay Gate Data and Slippage Calibration

1. **Execution replay gate** (promotion-grade) requires
   `execution_classification == "L3_VALIDATED"` from a real Bitfinex WS R0
   capture at `data/replay/hftbacktest/crypto/bitfinex/BTC_USD/BTC_USD_mbo.npz`
   with meta sidecar fields per `packages/crypto_lane/docs/CRYPTO_REPLAY_DATA.md`
   (`data_class: L3_MBO`, `source_feed: bitfinex_ws_r0`). Kraken
   `L2_DEPTH_VALIDATED` and Binance `L2_PROXY_ONLY` are diagnostic only and
   never satisfy the gate. Fixture-derived NPZ never satisfies the gate
   (§8 row K6).
2. **Queue model routing**: promotion replay runs `L3FifoQueueModel`
   (per CRYPTO_REPLAY_DATA.md routing) — `SquareProbQueueModel` is for
   L2 diagnostic paths only.
3. **Slippage calibration contract** (defect kf):
   - Queue-model parameters are fit against captured Bitfinex L3 MBO
     (ALPHA_CRYPTO.md C3), then re-fit against micro-live fills once C8
     produces them (§5.4).
   - The fit produces a **calibration artifact**
     (`runtime/crypto_latency/slippage_calibration.json`) recording fit date,
     input capture session IDs, sample counts, fitted parameters, and the
     acceptance statistic.
   - **Shadow envelope**: realized fill rate and slippage during paper-shadow
     must lie within ±2σ of the calibrated distribution (crypto instantiation
     of DEPLOYMENT.md §4.4 row 4).
   - **Staleness rule**: crypto bundle build fails if the calibration artifact
     is absent or predates the newest MBO/micro-live capture session used by
     the promoted sweep (§8 row K12).

---

## 7. Deployment Deltas vs DEPLOYMENT.md

DEPLOYMENT.md §1 (bundle), §2 (versioning), §3 (startup validation),
§5 (arm), §6 (rollback), §7 (audit) apply with these deltas:

| Item | CME (DEPLOYMENT.md) | Crypto delta |
|---|---|---|
| Manifest `symbol` | e.g. `MES` | Bitfinex venue order symbol `tBTCUSD` (replay NPZ symbol `BTC_USD`, routing key `BTCUSDT` — manifest carries the venue form; startup symbol-match per DEPLOYMENT.md §3 compares against the adapter's configured venue symbol) |
| `latency_ms_at_promotion` | CME latency summary / explicit `--latency-ms` | Drawn from `runtime/crypto_latency/latency_summary.json` `order_ack_p99_ms` when `measured=true`, else explicit `--latency-ms`; UNMEASURED default prohibited identically (LATENCY.md §10) |
| Release layout | `/root/hft3/releases/` on CHI404 | Same layout on the Contabo live host; `current` symlink semantics unchanged |
| Startup validation | DEPLOYMENT.md §3 table | Unchanged, executed by the crypto engine/deploy script on the Contabo host |
| Audit log | `runtime/validation/deployment_audit.jsonl` | **Separate lane-scoped file** `runtime/validation/crypto_deployment_audit.jsonl`, same hash-chain record format and event types (DEPLOYMENT.md §7) — separate file avoids cross-lane chain interleaving while keeping one verifier |
| Shadow window | 2026-01-01 → 2026-06-10 | Same embargo carried verbatim; see ALPHA_CRYPTO.md §4 |
| Kill-switch drill | CHI404_RUNTIME.md §9 | §4.3 drill on Bitfinex paper session |

---

## 8. Crypto No-Bugs Regime

CORRECTNESS.md §1 principle applies verbatim: **a rule without an enforcement
command is not a rule.** Rows marked *(to be created at Cn)* follow the
CORRECTNESS.md row-11 precedent — the row is binding now; the named test is a
campaign deliverable.

| # | Rule | Enforcement command / test path |
|---|------|---------------------------------|
| K1 | **Single-submission-gate proof, crypto adapter**: order-call counter unchanged after risk BLOCK; exactly one venue order-new call site across `packages/execution/adapters/crypto_*.py` | `python -m pytest tests/test_crypto_submission_gate.py -q` *(to be created at C6; flat under `tests/` like `tests/test_execution_interface_parity.py`)* + grep check: exactly one call site of the venue submit function |
| K2 | **Mode safety**: REPLAY forbids both crypto adapters; PAPER forbids `CryptoLiveBrokerAdapter`; factory is the only constructor path | `python -m pytest tests/test_crypto_mode_safety.py -q` *(to be created at C6)* — asserts forbidden-name tuples in `safety.assert_replay_safe` / `assert_paper_safe` include the crypto adapters |
| K3 | **Quality-flag honesty**: `perp_data_quality_flag`, `l2_data_quality_flag`, `vol_surface_quality_flag` null-gate their derived columns; production never sets a flag=1 without a real wired source | `python -m pytest tests/test_crypto_lane/test_normalize_correctness.py tests/test_crypto_lane/test_feature_builders.py -q` (exists, green) |
| K4 | **Silent-zero prohibition**: standalone `build_basis_features` / `build_deribit_vol_features` calls with quality flag=0 must flag or raise — never emit unflagged zeros | `python -m pytest tests/test_crypto_lane/test_feature_builders_silent_zero.py -q` *(to be created at C5)* |
| K5 | **PIT boundary**: NTP θ sign convention (θ = remote − local; subtract to convert), availability boundary, `MAX_CLOCK_DRIFT_MS` clamp | `python -m pytest tests/test_crypto_lane/test_clock_sync.py tests/test_crypto_lane/test_btc_node_feature_pit_alignment.py -q` (exists, green) |
| K6 | **Replay data-class honesty**: promotion path requires `L3_VALIDATED` from real (non-fixture) Bitfinex R0 NPZ; `L3FifoQueueModel` routing asserted | `python scripts/verify_crypto_replay_data.py` + `python -m pytest tests/test_crypto_l2/test_bitfinex_mbo_converter.py tests/test_crypto_l2/test_crypto_execution_validator.py -q` (exists; real-NPZ presence is the C1 gate) |
| K7 | **No synthetic venue RTT in promotion path**: promotion replay hard-fails when the resolved venue profile source matches `synthetic_calibrated:*`; only `live_measured:*` / `ws_rtt:*` are promotion-eligible | `python -m pytest tests/test_crypto_lane/test_latency_profile.py -q` (exists, green) + resolver hard-fail test *(to be created at C0)* |
| K8 | **Kill-switch drill**: `LIVE_KILL_SWITCH` fire → cancel-all submitted + halt within 1 s on a Bitfinex paper session | `python scripts/crypto_kill_drill.py --venue bitfinex_paper` exits 0 *(to be created at C7)* |
| K9 | **Forward-only labels / leakage controls** | `python -m pytest tests/test_crypto_lane/test_labels_forward_only.py tests/test_crypto_lane/test_holdout_and_leakage_controls.py -q` (exists, green) |
| K10 | **Validation honesty**: deflated Sharpe, BH, `n_trials`, `chi404_order_ack_status` fields present in smoke reports; holdout kill-gate active | `python -m pytest tests/test_crypto_lane/test_validation_honesty.py tests/test_crypto_lane/test_holdout_gate_stages.py -q` (exists, green) |
| K11 | **Lane decoupling**: no crypto → alpha-engine import leakage | `python -m pytest tests/test_crypto_lane/test_no_crypto_alpha_engine_imports.py -q` (exists, green) |
| K12 | **Slippage calibration freshness**: crypto bundle build fails when `runtime/crypto_latency/slippage_calibration.json` is absent or stale per §6.3 | `python -m pytest tests/test_crypto_lane/test_slippage_calibration.py -q` *(to be created at C3)* |

---

## 9. Crypto Known-Defect Ledger

Lane-scoped: this ledger gates **crypto** live arm (ALPHA_CRYPTO.md C12) and
must be **EMPTY** before it. The CME ledger (CORRECTNESS.md §3) gates CME arm;
neither blocks the other. Seeded from the verified 2026-06-10 state
(`packages/crypto_lane/docs/HANDOFF_2026_06_02.md` known-gaps + gap audit):

| ID | Component | Description | Status |
|----|-----------|-------------|--------|
| ka | `packages/execution/adapters/` | No crypto execution adapter exists; `adapter_factory.py` cannot produce a crypto paper/live adapter | **CLOSED** — C6 `cf36563` |
| kb | Crypto submission path | No risk-check enforcement on any crypto order path; `LIVE_KILL_SWITCH` not wired to crypto cancel-all (no kill-switch reference in `packages/crypto_lane/`) | **CLOSED** — C7 `762c1cd` |
| kc | `venue_profiles.json` | All venue RTT entries synthetic (`synthetic_calibrated:*`); `measure_live_ws_rtt` never run against a live venue; venue URL map (`latency_profile.py`) lacks `bitfinex` | **CLOSED (provisional)** — C0 `a00cb2b`; authoritative re-measurement at C9 |
| kd | Production ingest | `l2_data_quality_flag=0` in production — no live L2 source wired; only fixtures carry flag=1 (L2-derived features null-gated, hence silently absent) | **CLOSED** — C4 `95d92e3` |
| ke | `build_basis_features` / `build_deribit_vol_features` | Silent-zero hazard for standalone callers when quality flags=0 (safe only via `feature_matrix.py` null-gate) | **CLOSED** — C5 `925894e` |
| kf | Queue/slippage models | `SquareProbQueueModel` / `L3FifoQueueModel` parameters never fit to real exchange data; no calibration artifact exists | **CLOSED (provisional)** — C3 `68cd7ff`; re-fit vs micro-live fills after C8 (§6.3) |
| kg | Crypto latency | Bands `[5, 50, 200]` ms declared but unmeasured live; LATENCY.md §3 sweep-list TODO outstanding; no crypto `latency_summary.json` | **OPEN** — C0 done; C9 pending |

Closure mapping: ka→C6, kb→C7, kc→C0, kd→C4, ke→C5, kf→C3 (re-fit after C8),
kg→C0+C9 (see ALPHA_CRYPTO.md §2).

---

## 10. K-Lane Trigger and Tier Mapping

**K-lane** (analog of CORRECTNESS.md §4 C-lane): any commit that modifies a
path under `packages/crypto_lane/` or `packages/execution/adapters/crypto_*`
triggers, in addition to T0:

```
python -m pytest tests/test_crypto_lane/ tests/test_crypto_l2/ -q
```

| Regime row | T0 (every commit) | K-lane (per crypto commit) | T2 (weekly/manual) | T4 (before promotion) |
|------------|:-----------------:|:--------------------------:|:------------------:|:---------------------:|
| K1 submission gate | ✓ | ✓ | | |
| K2 mode safety | ✓ | ✓ | | |
| K3 quality-flag honesty | | ✓ | | |
| K4 silent-zero prohibition | | ✓ | | |
| K5 PIT boundary | | ✓ | | ✓ |
| K6 replay data-class honesty | | ✓ | | ✓ |
| K7 no-synthetic-RTT | | ✓ | | ✓ |
| K8 kill drill | | | ✓ | ✓ |
| K9 forward-only labels | | ✓ | | ✓ |
| K10 validation honesty | | ✓ | | ✓ |
| K11 lane decoupling | ✓ | ✓ | | |
| K12 calibration freshness | | | | ✓ |
