# PDF structural models — implementation specs

Source: [algorithmic_trading_strategy_development.pdf](../references/algorithmic_trading_strategy_development.pdf) (PDF_MODEL_1..7); [hft_framework_developer_prompt.pdf](../../hft_framework_developer_prompt.pdf) (PDF_MODEL_8..11).

Eleven models live in `features_engine/src/structural_models/`. Registry: `get_structural_models()`.
**Not** merged into `get_active_hypotheses()` (44 HYP unchanged → **55 total inventory**).

---

## PDF_MODEL_1 — Limit Order Book Pressure

| Field | Value |
|-------|-------|
| **model_name** | `PDF_MODEL_1` / `BookPressureModel` |
| **purpose** | Level-1 OFI, multi-level MLOFI, PCA deep-book pressure, spoof defense |
| **required_inputs** | BBO or L3 book top-M levels (price, qty per side) |
| **formulas** | Level-1 OFI event `e_n`; `OFI_k = Σ e_n`; `MLOFI^m = OF_{m,b} - OF_{m,a}`; PCA on MLOFI → PC1 |
| **intermediate_calculations** | Per-level bid/ask qty deltas; rolling OFI z-score; spoof = L1 direction vs PC1 conflict |
| **outputs** | `OFI_value`, `OFI_zscore`, `MLOFI_vector`, `MLOFI_PC1`, `book_pressure_direction`, `spoofing_risk_flag`, `OFI_smooth` |
| **execution_interpretation** | Positive OFI/PC1 → bid-side pressure; spoof flag → reduce quote size |
| **dependency_on_other_models** | None (standalone) |
| **reason_model_is_separate_or_combined** | Internal convergence: OFI + MLOFI + PCA + spoof in one model |

---

## PDF_MODEL_2 — Cross-Asset Lead-Lag

| Field | Value |
|-------|-------|
| **model_name** | `PDF_MODEL_2` / `CrossAssetLeadLagModel` |
| **purpose** | Cross-impact of leader OFI on target returns |
| **required_inputs** | Model 1 outputs per asset (own + leaders) |
| **formulas** | `r_{i,t+1} = α_i + Σ β OFI_{i,t} + Σ_{j≠i} Σ γ OFI_{j,t} + ε` |
| **intermediate_calculations** | Ridge/elastic-net fit on lagged OFI (offline cal + online score) |
| **outputs** | `leader_asset`, `target_asset`, `cross_impact_score`, `predicted_target_return`, `lead_lag_stability`, `signal_decay_curve` |
| **execution_interpretation** | High cross_impact_score → lean target with leader OFI sign |
| **dependency_on_other_models** | Consumes PDF_MODEL_1 only |
| **reason_model_is_separate_or_combined** | Separate registry entry; regression layer distinct from OFI math |

---

## PDF_MODEL_3 — VPIN Flow Toxicity

| Field | Value |
|-------|-------|
| **model_name** | `PDF_MODEL_3` / `VPINToxicityModel` |
| **purpose** | Volume-time flow toxicity via BVC and Student-t tails |
| **required_inputs** | Trade prices/volumes, bar mid returns |
| **formulas** | BVC `V_τ^B = V_τ Z(ΔP/σ)`; `VPIN = Σ|V_τ^B - V_τ^S| / (nV)` |
| **intermediate_calculations** | Volume buckets (200/day, 30 bars); Student-t CDF for BVC |
| **outputs** | `VPIN_value`, `VPIN_percentile`, `toxicity_regime`, `toxic_flow_alert`, `volatility_warning` |
| **execution_interpretation** | VPIN percentile ≥ 0.99 → widen spreads / reduce aggression |
| **dependency_on_other_models** | None (standalone) |
| **reason_model_is_separate_or_combined** | Standalone toxicity; consumed by Model 4 only |

---

## PDF_MODEL_4 — Hybrid Defensive Execution

| Field | Value |
|-------|-------|
| **model_name** | `PDF_MODEL_4` / `HybridExecutionModel` |
| **purpose** | Avellaneda-Stoikov reservation + OFI/VPIN hybrid drift |
| **required_inputs** | Mid, inventory, σ, T-t; Model 1 `OFI_smooth`; Model 3 `VPIN_value` |
| **formulas** | `r(t)=S_t-q_t γ σ²(T-t)`; `δ^a+δ^b=(2/γ)ln(1+γ/κ)`; `r*(t)=r(t)+λ_t OFI_smooth` |
| **intermediate_calculations** | λ_t scaled by VPIN; cancel/passive flags |
| **outputs** | `reservation_price`, `hybrid_reservation_price`, `optimal_bid`, `optimal_ask`, `spread_width`, `inventory_penalty`, `OFI_drift_component`, `VPIN_multiplier`, `cancel_quote_flag`, `passive_to_aggressive_flag` |
| **execution_interpretation** | Quote around r*(t); cancel when VPIN toxic + inventory extreme |
| **dependency_on_other_models** | Consumes Model 1 + Model 3 outputs only |
| **reason_model_is_separate_or_combined** | Convergence layer: AS + OFI + VPIN via struct reads, not merged codebases |

---

## PDF_MODEL_5 — Dealer Options Hedging

| Field | Value |
|-------|-------|
| **model_name** | `PDF_MODEL_5` / `DealerHedgingModel` |
| **purpose** | Black-Scholes gamma, dollar GEX, vanna, charm |
| **required_inputs** | Spot, strikes, IV, OI, time to expiry |
| **formulas** | BS C/P; Γ; dollar GEX; vanna ∂Δ/∂σ; charm ∂Δ/∂t |
| **intermediate_calculations** | Strike aggregation; zero-gamma crossing |
| **outputs** | `total_gex`, `gex_by_strike`, `vanna_exposure`, `charm_exposure`, `zero_gamma_level`, `dealer_hedging_pressure` |
| **execution_interpretation** | Negative GEX → vol amplification; zero-gamma as pivot |
| **dependency_on_other_models** | None |
| **reason_model_is_separate_or_combined** | Options chain math; no parity-lane coupling |

---

## PDF_MODEL_6 — Dow/YM Price-Weighted Index

| Field | Value |
|-------|-------|
| **model_name** | `PDF_MODEL_6` / `DowYMIndexModel` |
| **purpose** | Divisor-weighted index pressure from top constituents |
| **required_inputs** | Constituent prices; Model 1 OFI per constituent |
| **formulas** | `Index = Σ P_i / Divisor` |
| **intermediate_calculations** | Rank by price; aggregate top-component OFI |
| **outputs** | `component_price_weight`, `top_component_OFI`, `synthetic_Dow_pressure`, `YM_fair_pressure`, `constituent_to_YM_signal` |
| **execution_interpretation** | synthetic_Dow_pressure leads YM fair value drift |
| **dependency_on_other_models** | Consumes Model 1 only |
| **reason_model_is_separate_or_combined** | Index construction separate from single-asset OFI |

---

## PDF_MODEL_7 — Treasury CTD / Implied Repo

| Field | Value |
|-------|-------|
| **model_name** | `PDF_MODEL_7` / `TreasuryCTDModel` |
| **purpose** | Cheapest-to-deliver, delivery cost, implied repo |
| **required_inputs** | Futures price, bond prices, conversion factors (YAML basket) |
| **formulas** | Delivery cost per bond; CTD = argmin cost; implied repo |
| **intermediate_calculations** | CTD switch threshold; quality option pressure |
| **outputs** | `current_CTD`, `delivery_cost_by_bond`, `implied_repo_by_bond`, `CTD_switch_threshold`, `quality_option_pressure`, `futures_basis_signal` |
| **execution_interpretation** | CTD switch → basis jump; repo spread vs funding |
| **dependency_on_other_models** | None (fully standalone) |
| **reason_model_is_separate_or_combined** | Fixed-income delivery math; no equity book overlap |

---

## HFT framework models (PDF_MODEL_8..11)

Source: [hft_framework_developer_prompt.pdf](../../hft_framework_developer_prompt.pdf) (also `docs/references/`)

| Model | Class | Module | Signal / output |
|-------|-------|--------|-----------------|
| **PDF_MODEL_8** | `TransferEntropyModel` | Transfer Entropy lead-lag | `transfer_entropy`, `aggressive_liquidity_signal` |
| **PDF_MODEL_9** | `QuantumSpreadDefenseModel` | Spread eigenstate P(Δ), Bessel I₀ | `collapse_risk`, `cancel_all_quotes` |
| **PDF_MODEL_10** | `StochasticThermoModel` | Gibbs ensemble, F(β) | `free_energy`, `mean_reversion_signal` |
| **PDF_MODEL_11** | `HawkesToxicFlowModel` | Multivariate Hawkes → AS γ skew | `toxic_cascade_score`, `risk_aversion_gamma` |

**Inventory:** 44 HYP + **11 PDF** = **55 total** workbench models.

PDF_MODEL_11 depends on PDF_MODEL_4 (Avellaneda-Stoikov reservation skew per Module 4).
