# Hypothesis → feature slot map (fs_v1 reads)

Authority: `packages/features_engine/src/hypotheses/modules.py`, `vix_modules.py`, `model_registry.yaml`.

Each row is one **hypothesis model**. All models run inside the 64-dim `fs_v1` pipeline; **signal logic** reads only the listed slots (plus context gates). Slot names map to [specs/FEATURES.md](../../specs/FEATURES.md).

| hyp_id | slug | gates (summary) | feature_slots_read |
|--------|------|-----------------|-------------------|
| 1 | SECOND_WAVE_CONTINUATION | — | `aggressor_volume_imbalance` |
| 2 | STOP_RUN_EXHAUSTION_FADE | — | `aggressor_volume_imbalance`, `near_touch_cancel_pressure`, `book_slope` |
| 3 | LIQUIDITY_VACUUM_CONTINUATION | — | `liquidity_vacuum_score`, `aggressor_volume_imbalance` |
| 4 | DEPTH_REFILL_IMBALANCE | — | `book_slope_change`, `cancel_to_add_ratio` |
| 5 | SPREAD_BLOWOUT_RECOMPRESSION | — | `spread_stress`, `book_slope` |
| 6 | AGGRESSOR_DECELERATION_FADE | — | `aggressor_volume_imbalance`, `book_slope` |
| 7 | FORCED_LIQUIDATION_CASCADE | — | `aggressor_volume_imbalance`, `spread_stress`, `cancel_to_add_ratio` |
| 8 | FALSE_BREAKOUT_TRAP | — | `spread_stress_elevated`, `aggressor_volume_imbalance`, `book_slope` |
| 9 | CANCEL_STORM_BEFORE_MOVE | — | `cancel_to_add_ratio`, `near_touch_cancel_pressure`, `book_slope` |
| 10 | QUEUE_DEPLETION_TRIGGER | — | `queue_depletion_rate_bid`, `queue_depletion_rate_ask` |
| 11 | BOOK_SLOPE_COLLAPSE | — | `book_slope`, `book_slope_change` |
| 12 | ABSORPTION_FADE | — | `absorption_score`, `aggressor_volume_imbalance` |
| 13 | ICEBERG_RELOAD_DETECTION | — | `iceberg_reload_score` |
| 14 | LIQUIDITY_DEFENSE_BREAK | — | `reload_drop_score`, `aggressor_volume_imbalance` |
| 15 | ONE_SIDED_ADD_CANCEL_IMBALANCE | — | `bid_add_cancel_ratio`, `ask_add_cancel_ratio` |
| 16 | ES_MES_LEAD_LAG | needs ES cross-asset leg | primary + ES: `aggressor_volume_imbalance` |
| 17 | NQ_MNQ_LEAD_LAG | needs NQ leg | primary + NQ: `aggressor_volume_imbalance` |
| 18 | ES_NQ_DIVERGENCE_SNAPBACK | needs ES+NQ | ES/NQ: `aggressor_volume_imbalance` |
| 19 | ZN_ZB_ES_NQ_MACRO_IMPULSE | needs ZN leg | primary + ZN: `aggressor_volume_imbalance` |
| 20 | MICRO_CONTRACT_RETAIL_LAG | silent without ES | primary + ES via `micro_leader_divergence` |
| 21 | ROUND_NUMBER_STOP_SWEEP | — | `distance_to_round_number`, `aggressor_volume_imbalance` |
| 22 | PRIOR_HIGH_LOW_BREAKOUT_TRAP | — | `is_breaking_session_level`, `aggressor_volume_imbalance` |
| 23 | OPENING_CANDLE_CHASE | `event_context==CASH_EQUITY_OPEN` | `aggressor_volume_imbalance`, `spread_stress` |
| 24 | VWAP_DEFENSE_BREAK | — | `distance_to_vwap`, `iceberg_reload_score`, `aggressor_volume_imbalance` |
| 25 | DOM_ILLUSION_TRAP | — | `near_touch_cancel_pressure`, `aggressor_volume_imbalance` |
| 26 | LATE_CANDLE_ENTRY_FADE | `regime_state==trend_continuation` | `aggressor_volume_imbalance` |
| 27 | STOP_LOSS_CASCADE_CONTINUATION | `regime_state==stop_cascade` | `book_slope`, `aggressor_volume_imbalance` |
| 28 | PANIC_MARKET_ORDER_SPREAD_TAX | `volatility_state==HIGH` | `spread_stress` (stub returns 0) |
| 29 | END_OF_DAY_FORCED_FLATTEN_FLOW | prop flatten contexts | `aggressor_volume_imbalance` |
| 30 | CUTOFF_PANIC_EXITS | prop flatten contexts | `cutoff_pressure_score` |
| 31 | NO_OVERNIGHT_INVENTORY_SQUEEZE | `FRIDAY_CLOSE` | `aggressor_volume_imbalance` |
| 32 | DAILY_LOSS_LIMIT_DEFENSE | `prop_cohort_active()` | `cutoff_pressure_score`; ES divergence gate |
| 33 | TRAILING_DRAWDOWN_PRESSURE | `regime_state==trend_continuation` | `aggressor_volume_imbalance` |
| 34 | PROFIT_LOCK_BEHAVIOR | prop flatten + trend regime | `aggressor_volume_imbalance` |
| 35 | MAX_CONTRACT_CROWDING_IN_MICROS | — | `max_contract_trade_imbalance` |
| 36 | PROP_RESET_REOPEN_WINDOW | `PROP_REOPEN` | `prop_reentry_score` |
| 37 | FRIDAY_WEEKEND_DERISKING | `FRIDAY_CLOSE` | `aggressor_volume_imbalance` |
| 38 | ECONOMIC_EVENT_RESTRICTION_FLATTENING | `event_context.endswith('_TIGHT')` | `news_restriction_flatten_score` |
| 39 | QUOTE_PULL_BEFORE_VOLATILITY | `CPI_TIGHT` (stub) | `book_slope_change` (unused) |
| 40 | REQUOTE_RACE_AFTER_SHOCK | `regime_state==event_shock` | `book_slope_change` |
| 41 | THIN_BOOK_CONTINUATION | — | `spread_stress`, `cancel_to_add_ratio`, `aggressor_volume_imbalance` |
| 42 | PASSIVE_TRAP_FILL | — | `aggressor_volume_imbalance` |
| 43 | REBATE_TRAP_AVOIDANCE | — | none (returns 0) |
| 44 | SPREAD_REGIME_CHANGE | `volatility_state==HIGH` | `spread_stress` (stub returns 0) |
| 45 | GHOST_ROUTE | `expected_edge_ticks>0` | `macro_shadow_decay`, `micro_stale_quote_zscore`, `macro_nofi`, `expected_edge_ticks` |
| 46 | VIX_SPIKE_EVENT_FADE | VIX leg + tight window + vol spike | VIX vol slots; `aggressor_volume_imbalance` |
| 47 | VIX_QUOTE_PULL_LIQUIDITY_VACUUM | VIX quote pull + vacuum | VIX accel/spread; `liquidity_vacuum_score`, `book_slope` |
| 48 | VIX_IMPLIED_REALIZED_GAP | VIX leg | VIX bipower; `realized_vol_state`, `book_slope`; regime posterior |
| 49 | VIX_DEPTH_IMBALANCE_DIRECTION | VIX + tight/macro window | VIX depth imbalance; `spread_stress` |
| 50 | VIX_LEVEL_CONDITIONED_CONTINUATION | VIX ATM + event_shock regime | VIX ATM; `aggressor_volume_imbalance`; regime posterior |

**Cross-asset hypotheses (16–20):** require multi-symbol MBO in replay; single-symbol NPZ may yield zero signal by design.

**VIX hypotheses (46–50):** require VIX options feature leg in `cross_asset_features` or dedicated VIX feed; missing leg → no signal.

**PDF structural models (slots 50–63):** not listed here; registered separately via `get_structural_models()`. Do not merge into HYP VectorBT units without explicit spec + C++ parity review.
