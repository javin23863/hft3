# HFT3 Developer Notes — Requirements to Code Mapping

## How to Use This Document

This file maps each mathematical invariant, architectural constraint, and documented requirement from the PDF ontology to specific code modules. When making changes, consult the relevant section to ensure compliance.

## Mathematical Invariants (from BLUEPRINT.md §2, REVIEWER_CHARTER.md Pass B)

| # | Invariant | Documents | Enforced by | Status |
|---|-----------|-----------|-------------|--------|
| B1 | Filtration F_t — only info up to time t | mathematical_model.pdf | `MarketStatePipeline.process_event()` (sequential), `MBOFeatureExtractor` (rolling state) | ✅ Enforced |
| B2 | Event-time correctness — MBO marked events | mathematical_model.pdf | `npz_feed.iter_mbo_events()`, `ReplaySession` preserves exchange order | ✅ Enforced |
| B3 | No lookahead / data leakage | mathematical_model.pdf, VALIDATION_HONESTY.md | `WalkForwardEnforcer` (events.py year parsing), sequential MSP | ✅ Enforced |
| B4 | Walk-forward discipline — Discovery 2018-20, Confirmation 2021-22, Holdout 2023-24, Recent 2025, Sim Shadow 2026+ | BLUEPRINT.md §8, WALK_FORWARD_CAMPAIGNS.md | `packages/hft3_pipeline/walk_forward.py` gated in `stage_vectorbt_filter`, `stage_hft_truth` | ✅ Enforced |
| B5 | Execution realism — latency 0.5-10ms, queue models, fees, net edge after costs | developer_handoff.pdf, LATENCY_ARCHITECTURE.md | `hft_backtest_builder.py` (constant_latency + queue model), `_load_latency_config` from CHI404 `cpp_latency_profile.yaml` | ✅ Enforced |
| B6 | Regime P(Z_t|F_t) — probabilistic latent states | mathematical_model.pdf | `RegimeFilter.update()` in `market_state_pipeline.py` | ✅ Enforced |
| B7 | Trial vs production data lanes — quarantined trial paths | developer_handoff.pdf, KNOWN_GAPS.md | `data/npz/` production only; trial paths under `data/replay/hftbacktest/rithmic_trial/` | ✅ Enforced |
| B8 | Production failure states — stale/halt/clock/position/loss | production_implementation.pdf | Stub in `stage_trade_manager` (risk layer); full CHI404 path not workstation | ⚠️ Partial |

## Pipeline Stages (0–9)

| Stage | File | Purpose | Key Enforcements |
|-------|------|---------|------------------|
| 0 Inventory | `stages.py:45` | Scan repo capabilities | None |
| 1 Data readiness | `stages.py:49` | Check NPZ exists on disk | None |
| 2 Data fingerprint | `stages.py:80` | SHA256 partial hash of raw NPZ | PIT check, leakage check |
| 3 VectorBT filter | `stages.py:114` | Fast sweep using real MSP features (B4) | **Walk-forward check**, evaluate-only on holdout |
| 4 HFT truth | `stages.py:428` | ReplaySession via hftbacktest (B5) | **Walk-forward check**, CHI404 latency loaded from config |
| 5 Full metrics | `stages.py:579` | Scorecard from model_metrics | Grade A-F |
| 6 Robustness | `stages.py:602` | WFC stub for campaigns | Single-event → SKIPPED |
| 7 Promotion | `stages.py:615` | Certification registry | Mode-based gating |
| 8 Trade Manager | `stages.py:692` | Simulation session | Risk config |
| 9 Workbench truth | `stages.py:833` | Manifest finalisation | None |

## Walk-Forward Periods (B4)

Defined in `packages/hft3_pipeline/walk_forward.py`:

| Period | Years | VectorBT Tuning | HFT Evaluation | Promotion Eligible |
|--------|-------|-----------------|----------------|-------------------|
| Discovery | 2018-2020 | ✅ Allowed | ✅ Allowed | ✅ (if passes) |
| Confirmation | 2021-2022 | ❌ Evaluate-only (no param sweep) | ✅ Allowed | ✅ (if passes) |
| Holdout | 2023-2024 | ❌ Evaluate-only | ✅ Allowed | ⚠️ (if locked params were used) |
| Recent holdout | 2025 | ❌ Evaluate-only | ✅ Allowed | ⚠️ |
| Sim shadow | 2026+ | ❌ Blocked (CHI404 only) | ❌ Blocked (workstation) | ❌ |

When a holdout event is detected:
- `stage_vectorbt_filter` reduces the param sweep to a single default set
- `stage_hft_truth` processes normally with loaded latency config
- Promotions from evaluate-only periods are marked QUARANTINED unless frozen-params were proven on Discovery

## Event ID → Year Parsing

Events use Databento release dates in their IDs: `CPI_2024_09_11_TIGHT` → year 2024.  
Parser: `walk_forward.extract_event_year()` uses regex `(?:^|_)(\d{4})_\d{2}_\d{2}`.

## Latency Architecture (B5)

Latency is loaded from `apps/workbench/config/cpp_latency_profile.yaml` at runtime by `_load_latency_config()`:

| Source | Field | Used For |
|--------|-------|----------|
| `feed_delay.p99_us` (4094 µs) | Primary latency | `hftbacktest_config.latency_ms` |
| `cpp_decision_compute.p50_us` (11 µs) | Compute time (informational) | `decision_compute_p50_us` |
| CHI404 `latency_summary.json` | Production truth | Used on CHI404; workstation uses profile default |

Hardcoded fallback: `1.0 ms` if config file is missing.

## 55-Model Registry

Defined in `packages/features_engine/config/model_registry.yaml`:

- **44 Hypotheses** (HYP_1–HYP_44) — microstructure alpha models with `.evaluate(state)` signals
- **11 PDF structural models** (PDF_MODEL_1–PDF_MODEL_11) — book pressure, VPIN, hybrid execution, options hedging, etc.

Workbench slugs are canonical. Legacy ids (`HYP_N` / `PDF_MODEL_N`) are deprecated with `DeprecationWarning`.

## Run Modes

| Mode | `.env` | Synthetic Allowed? | Promotion Eligible? | Walk-Forward Enforced? |
|------|--------|-------------------|---------------------|----------------------|
| REAL_RESEARCH | `run_mode=REAL_RESEARCH` | ❌ | ✅ | ✅ Full |
| PAPER_REPLAY | `run_mode=PAPER_REPLAY` | ❌ | ✅ (CHI404) | ✅ Full |
| FIXTURE_CI | `run_mode=FIXTURE_CI` | ✅ | ❌ | ⚠️ (bypassed) |
| PERFORMANCE_BENCHMARK | `run_mode=PERFORMANCE_BENCHMARK` | ✅ | ❌ | ⚠️ (bypassed) |
| DEBUG | `run_mode=DEBUG` | ✅ | ❌ | ⚠️ (bypassed) |

## Data Layout (from HANDOFF_CME_DATA_AND_BACKTEST.md)

```
data/
  npz/                        # Derived replay NPZ per event × symbol
  mbo_release/                # Raw MBO + VIX raw per event
  sensors/                    # VIX sensor parquets
  equities/                   # Low-float equities lane (quarantined)
  crypto/                     # Crypto lane (quarantined)
  options/                    # Options parity lane (quarantined)
  replay/
    hftbacktest/
      rithmic_trial/          # Trial NPZ (quarantined — NOT Databento)
```

## Certification Tiers (BACKTESTER_CERTIFICATION.md)

| Tier | Scope | Command |
|------|-------|---------|
| T0 Fast | Every commit | `python -m pytest tests/backtester_validation/fast -q` |
| T1 Stamp | Every backtest | Automatic `build_certification_stamp()` |
| T2 Full | Weekly / after core change | `bash scripts/run_backtester_certification_full.sh` |
| T3 Staleness | Every stamp + promotion | `hft3.validation.certification_staleness` |
| T4 Champion | Before promotion | `bash scripts/check_champion_promotion_gate.sh` |

## Known Gaps (from KNOWN_GAPS.md)

| ID | Issue | Priority | Where |
|----|-------|----------|-------|
| I-01 | Macro auction uses test fixture when no real file | P0 | `imbalance/auction_events.py` |
| I-02 | Ablation = wrapper boost on hypothesis score, not toggling feature slots 34–37 | P0 | `imbalance/apply.py` |
| I-03 | Example macro replay: 0 PnL, 0 delta across ablation modes | P0 | Replay + hypothesis path |
| I-05 | C++ hot path no imbalance v1 slots | P1 | `rithmic_gateway/` |
| C-02 | Most slugs bound to CPI_TIGHT/NFP_TIGHT only | P1 | `model_event_binding.yaml` |
| P-01 | `pull-decadal` fails: undefined `daily_coverage_calendar_days` | P1 | `equities_lane/` |
| P-02 | `audit_all_research_data.py` ready flag misleading | P2 | `scripts/` |
| I-06 | Fast ablation = 4 modes | P2 | workbench CLI |

## Scope-Green Verify Commands (from VALIDATION_HONESTY.md)

| Area | Command |
|------|---------|
| Pipeline core | `python -m pytest tests/test_pipeline_e2e.py tests/test_pipeline_integration.py -q` |
| Features engine | `python -m pytest tests/test_feature_parity.py tests/test_regime_pipeline.py -q` |
| Replay + backtest | `python -m pytest tests/test_run_event_replay.py tests/test_replay_clock_order_timestamps.py -q` |
| Walk-forward | `python -m pytest tests/test_walk_forward.py -q` (if exists) |
| Repo-wide | `python -m pytest -q` or `scripts/run_agent_verify.ps1` |

## Document Index (for quick reference)

| File | What It Contains |
|------|-----------------|
| `BLUEPRINT.md` | System spec: mathematical model, architecture, validation |
| `KNOWN_GAPS.md` | Single billboard of broken/missing items |
| `VALIDATION_HONESTY.md` | Verification status handoff contract |
| `REVIEWER_CHARTER.md` | Pass A + B code review contract |
| `PIPELINE_CHECKLIST.md` | Pipeline stage state (update after changes) |
| `docs/vault/RESEARCH_ENTRYPOINTS.md` | Canonical script order |
| `docs/vault/BACKTESTER_CERTIFICATION.md` | T0-T4 certification tiers |
| `docs/vault/ECONOMIC_EVENT_UNIVERSE.md` | Macro calendar system |
| `docs/workbench/WALK_FORWARD_CAMPAIGNS.md` | B4 period config and CLI |
| `docs/workbench/LATENCY_ARCHITECTURE.md` | Latency measurement authority |
| `docs/workbench/HOT_MEMORY_UNIVERSE.md` | Market-state HOT/WARM/COLD tiers |
| `docs/references/MANIFEST.md` | PDF citation mapping |
| `HANDOFF_CME_DATA_AND_BACKTEST.md` | Data operator handoff |

## Key Source Files

| File | Purpose |
|------|---------|
| `packages/hft3_pipeline/__main__.py` | CLI entry point |
| `packages/hft3_pipeline/stages.py` | All 10 stages |
| `packages/hft3_pipeline/run_mode.py` | Run mode gating |
| `packages/hft3_pipeline/walk_forward.py` | B4 walk-forward enforcement |
| `packages/hft3_pipeline/manifest.py` | Pipeline artifact schemas |
| `packages/features_engine/src/pipeline/market_state_pipeline.py` | Core X_t builder |
| `packages/features_engine/src/regime/event_context.py` | E_t resolution |
| `packages/features_engine/src/hypotheses/modules.py` | All 44 hypothesis models |
| `packages/features_engine/config/model_registry.yaml` | 55-model registry |
| `packages/backtest_pipeline/src/hft_backtest_builder.py` | HftBacktest asset config |
| `packages/backtest_pipeline/src/replay_matrix.py` | ReplaySession execution |
| `packages/replay/market_data_adapter.py` | NPZ → MSP bridge |
| `configs/model_search_spaces.yaml` | Per-model param spaces |
| `apps/workbench/config/walk_forward.yaml` | Walk-forward period dates |
| `apps/workbench/config/cpp_latency_profile.yaml` | CHI404 latency distributions |
| `apps/workbench/config/models.yaml` | Per-model latency/execution overrides |
