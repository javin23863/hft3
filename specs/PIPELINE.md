# PIPELINE.md — Canonical Research-to-Live Pipeline

Version: 2026-06-10. Stage-skip flags are prohibited. REPLAY-mode audit-only
is the one sanctioned difference between modes (see §8).

---

## 1. Ingest

Source: Databento GLBX.MDP3 MBO schema, accessed via
`packages/data_system/src/databento_client.py` (`DatabentoResearchClient`).

- Auth: `DATABENTO_API_KEY` env var; raises on missing key.
- Budget gate: `metadata.get_cost()` called before every download;
  `BudgetManager.check_request()` enforces hard and operating caps.
  Dry-run (cost estimate only) is mandatory before purchase.
- Download: `timeseries.get_range()` → raw `.dbn.zst` file.
- Convert: `packages/backtest_pipeline/src/converter.py`
  (`DatabentoConverter.convert_file`) calls
  `hftbacktest.data.utils.databento.convert()`.
- Output path: `data/npz/{symbol}_{event_id}_mbo.npz`
  (canonical form per `packages/data_system/src/npz_resolver.py`
  `npz_path_for()`).
- Fallback: `npz_resolver.resolve_npz_for_event()` walks
  `PDF_PRIMARY_FALLBACK_ORDER = ("ES.v.0", "MNQ.v.0", "NQ.v.0")` when the
  requested symbol file is absent.

Crypto ingest path: see DATA_LAKE.md §4. Converts via the crypto_lane pipeline;
same `.npz` format, same resolver conventions.

---

## 2. Leakage-Audited Features / Labels

Source: `packages/decision_engine/python/src/targets.py`.

- `build_labels_frame()` uses vectorized `np.searchsorted` per horizon
  (O(N log N) total); horizons: `HORIZONS_MS = [100, 250, 500, 1000, 5000,
  15000, 60000]` ms.
- `leakage_audit()` validates every `y_return_*` column: `future_idx` must be
  strictly greater than the row index for every valid row.
- On failure: `ValueError("Leakage audit failed on label frame")` — pipeline
  halts; no silent pass-through.
- Timestamps must be monotonic non-decreasing; enforced with `np.diff(ts) >= 0`
  check (not dropped under `-O`).

---

## 3. Full-Matrix Hypothesis Screen

Source: `packages/backtest_pipeline/src/replay_matrix.py`.

- Entry: `run_all_hypotheses_replay(hypotheses, npz_path, latency_ms, ...)` —
  iterates all active hypotheses.
- Matrix sweep: `run_latency_matrix_replay()` — all latency bands × all
  hypotheses.
- Fills simulated via `ReplaySession` (see §3a); queue-model fill simulation
  (`LogProbQueueModel2` or `SquareProbQueueModel`) applied at order time by
  `HashMapMarketDepthBacktest`.
- CME latency bands: `LATENCY_BANDS_MS = [0.5, 1.0, 2.0, 5.0, 10.0]` (defined
  in `packages/backtest_pipeline/src/hft_backtest_builder.py`).
- Crypto latency bands: `[5, 50, 200]` ms defaults (per builder functions in
  `packages/backtest_pipeline/src/crypto_hft_builder.py`).
- Events corpus: `packages/data_system/config/events.csv` (55 rows: 33 NFP +
  19 CPI + 3 PROP_FLATTEN_TOPSTEP).

### 3a. ReplaySession

Config: `packages/replay/replay_session.py` `ReplaySessionConfig`.

- `feature_latency_ms` defaults to `latency_ms` when `None`; the feature clock
  is shifted back by `feat_latency_ns` to model feed staleness.
- Cross-asset symbols injected via `cross_asset_npz` / `cross_asset_events`
  dicts; secondary `HistoricalReplayMarketDataAdapter` built per symbol.
- Mode locked to `"REPLAY"`: `safety.assert_replay_safe()` raises if a
  live-capable adapter is present.
- Every run emits a stamped audit JSON to `runtime/replay_audits/`.

---

## 4. Multiple-Testing Correction

Source: `packages/decision_engine/python/src/multiple_testing_correction.py`
(`MultipleTestingGate`).

- Methods available: `bonferroni`, `holm` (Holm-Bonferroni, default),
  `benjamini_hochberg`.
- Input: list of `HypothesisTestResult` with p-value computed from one-sample
  t-test on `trade_pnls`.
- Output: `ChampionReport` with `passed_slugs` / `failed_slugs` lists.
- Hypotheses with fewer than 5 trades are excluded from the test pool.

---

## 5. Walk-Forward Validation

Source: `packages/decision_engine/python/src/walk_forward.py`
(`WalkForwardValidator`).

- Periods: Discovery 2018–2020, Confirmation 2021–2022, Holdout 2023–2024,
  Recent holdout 2025.
- Discovery has an internal OOS kill-gate: last `discovery_validation_fraction`
  (default 0.33) of the Discovery period is held out; model trained on
  first two-thirds only; gate fires if `net_expectancy <= 0` on that split.
- `window_mode`: `"expanding"` (default, train grows from 2018) or `"sliding"`
  (fixed `sliding_window_years` width).
- Purge/embargo: `purge_days` rows dropped from tail of training data;
  `embargo_days` rows skipped from head of eval data at every boundary.
- OOS kill-gate: `net_expectancy <= 0` on any period → `status = "FAIL"`,
  pipeline returns immediately.

---

## 6. Promotion Registry + Certification Stamps

Sources:
- `packages/hft3/validation/research_stamp.py` (`build_certification_stamp()`)
- `packages/hft3/validation/certification_registry.py`
  (`CertificationRecord`, `load_registry`, `save_registry`)

Stamp fields (embedded in result JSON): `status` (MISSING/STALE/GREEN/YELLOW/
RED), `certification_run_id`, `certification_commit`, `current_commit`,
`stale`, `changed_core_files`, `promotion_eligible`, `promotion_label`.

`promotion_label` values:
- `PROMOTION_ELIGIBLE_FROM_BACKTESTER_SIDE`: GREEN + not stale.
- `STALE_CERTIFICATION`: GREEN but core files changed since certification.
- `RESEARCH_ONLY`: YELLOW.
- `NOT_TRUSTED` / `UNCERTIFIED`: RED or MISSING.

Nothing trades without a `promotion_eligible=True` stamp from a current GREEN
certification.

Registry stored at `runtime/validation/certification_registry.json` with
append-only JSONL audit log at
`runtime/validation/certification_registry.jsonl` (SHA-256 hash chain).

---

## 7. Sim Shadow → Paper → Live

Safety enforced by `packages/execution/production_safety.py`
(`ProductionSafetyOrchestrator`).

PAPER mode runs `pre_trade_check()` identically to LIVE (both use
`enforce=True` path). REPLAY mode is audit-only (`enforce=False`).

`packages/execution/safety.py`:
- `assert_replay_safe()`: forbids `PaperBrokerAdapter`, `LiveBrokerAdapter`,
  `RithmicApiConnector` in REPLAY sessions.
- `assert_paper_safe()`: forbids `LiveBrokerAdapter` in PAPER mode.
- `assert_live_config()`: requires `LIVE_MAX_ORDER_SIZE`, `LIVE_DAILY_LOSS_LIMIT`,
  `LIVE_KILL_SWITCH`, `LIVE_RISK_ENABLED`.

Five safety monitors (priority order): DisconnectMonitor, DailyLossLimitFlatten,
PositionMismatchGuard, ClockDriftMonitor, StaleDataMonitor.

---

## 8. Mode Distinction

REPLAY is the one sanctioned mode difference. In REPLAY:
- Safety monitors run in audit-only mode (log warnings, no halts).
- Live-capable adapters are forbidden (`assert_replay_safe()`).
- Certification stamps are still generated and attached to audit outputs.

Stage-skip flags are prohibited. All upstream stages (ingest → labels →
matrix screen → multiple-testing → walk-forward → promotion stamp) must
complete before any downstream stage runs. Partial pipelines are not supported.

---

## Lanes

Stocks and options are one lane (equities); they share a single IBKR Web API
access path, the same latency floor, and the same promotion requirement. The
lane competes better-than-retail: IBKR provides no DMA, so latency is modelled
honestly against the Web API round-trip floor — slower results never block
alpha, but optimistic claims below the 5 ms floor are rejected. Promotion
requires a shadow run on an IBKR paper account via the Web API paper endpoint;
no TWS or IB Gateway GUI is present anywhere in the lane.

| Lane | Access | Capability | Latency policy | Promotion |
|---|---|---|---|---|
| cme_futures | Rithmic/DMA path | true HFT (proof required) | exact swept bands + measured ack | sim shadow CHI404 |
| crypto | node-direct | true HFT (proof required) | exact swept bands | sim shadow |
| equities (stocks+options) | IBKR Web API (OAuth headless / clientportal.gw), no DMA, no GUI | better-than-retail speed advantage | floor 5 ms (re-measure from Web API round-trip); slower never blocks; optimistic claims rejected | IBKR paper shadow via Web API paper account |
