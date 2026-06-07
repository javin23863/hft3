# Ghost Route Backtest Report

Classification: `WATCHLIST`

Ghost Route is registered and its standalone research/backtest module exists,
but no historical CME MDP 3.0 Level 3 MBO replay has been run for this report
artifact yet.

## Current Evidence

- Macro/micro pairs: `ES/MES`, `NQ/MNQ`, `YM/MYM`
- Wire-to-wire latency assumption: `23us`
- External routing authorized: `false`
- Acceptance requires measured compute latency, fees, partial fills, missed
  fills, slippage, adverse selection, realistic FAK simulation, and purged
  walk-forward validation.

## Classification Rule

Do not upgrade this report to `PASS` unless latency-adjusted and cost-adjusted
positive expectancy survives the full robustness and execution realism gates.
