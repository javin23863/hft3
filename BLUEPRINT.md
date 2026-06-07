# Chicago CME Microstructure Model - Developer Handoff

*This blueprint is based on the provided PDF specification for a professional, mathematically governed futures microstructure system.*

## 1. Executive specification
Build a complete CME futures microstructure research and execution stack. The output is a validated decision engine that estimates action value under latency, queue position, fill probability, transaction cost, adverse selection, inventory risk, and tail loss.

## 2. Mathematical foundation
* Rigorous probability, stochastic processes, statistics, econometrics/time series, numerical analysis, optimization/control, market microstructure, and robust systems engineering.
* **Non-negotiable mathematical invariants**: 
  * Filtration integrity (using only information available up to time $t$)
  * Event-time correctness (MBO order-flow is asynchronous marked events)
  * Execution realism (predicted edge evaluated after all transaction/latency costs)
  * Model agnosticism
  * Walk-forward evaluation (no random train/test splits across time)

## 3. Pure mathematical model
A partially observed marked-point-process stochastic-control model.
* **Information set and state**: $F_t$ (info up to $t$), $X_t$ containing book state, queue state, inventory, latent regime, latency, and event-state label (e.g. CPI, NFP).
* **Marked event process**: MBO actions are marked events with self-excitation / cross-excitation.
* **Latent regime posterior**: Estimates probability of being in regimes like `event_shock`, `liquidity_vacuum`, `stop_cascade`, etc.
* **Action-value objective**: $EV_t(a) = P_{fill}(a) * E[PnL | fill, a] - (1 - P_{fill}(a)) * C_{miss}(a) - Costs$
* **Position sizing**: Bounded by liquidity, margin, expected shortfall, and model capacity.

## 4. Live Architecture
* **Server**: True Chicago/Aurora-proximity bare metal (Dedicated CPU/RAM/NVMe).
* **Network**: Dedicated public IPv4, stable route to Rithmic Chicago/Aurora.
* **Rithmic adapter**: Connect to Rithmic, consume market data/depth/MBO.
* **Execution gateway**: Receive model actions, run pre-trade risk, construct order intents.
* **Model runtime**: Hot paths in Rust/C++/C#; Python/Numba allowed for research paths.

## 5. Historical data plan
Historical research uses **Databento GLBX.MDP3 MBO data** and **HftBacktest replay**.
Primary study from 2018-present.
* **Data pull sequence**: Cost calibration -> Core research -> Macro confirmation -> Full clean expansion -> Final holdout.

## 6. HftBacktest pipeline
Converts Databento MBO files into `.npz` arrays for HftBacktest.
Uses queue models (e.g., `LogProbQueueModel2`) and latency bands (0.5ms - 10ms).

## 7. Feature set and Hypothesis Families
Features computed using only data up to timestamp $t$. Include:
* Order-flow (aggressor volume)
* Depth/queue (top levels, refill rate)
* Liquidity stress (spread stress, vacuum score)
* Lead-lag, Absorption, Event-specific pressures.

**44 Hypothesis families** included for testing (e.g., Stop-run exhaustion fade, Depth-refill imbalance, Spread blowout, VWAP defense/break, etc.).

**7 PDF structural models** (`PDF_MODEL_1` … `PDF_MODEL_7`) from [Algorithmic Trading Strategy Development](docs/references/algorithmic_trading_strategy_development.pdf) live in a separate signal layer (`features_engine/src/structural_models/`). Total research inventory: **51** (44 HYP + 7 PDF). See [docs/structural_models/PDF_MODELS.md](docs/structural_models/PDF_MODELS.md).

## 8. Validation standard
Strict walk-forward periods:
* Discovery: 2018-2020
* Confirmation: 2021-2022
* Holdout: 2023-2024
* Recent holdout: 2025-present
* External/sim shadow: latest 60 CME days

## 9. Production failure states
Mathematical system safety limits including: stale market data halt, disconnect halt, clock drift halt, position mismatch block, and daily loss limit flatten.

---
*Refer to `chicago_cme_microstructure_a_plus_developer_handoff.pdf` for the full, detailed specification.*