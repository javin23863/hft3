# Model Registry Metadata

Authority: `packages/features_engine/config/model_registry.yaml`,
`docs/project/FEATURE_LITERATURE_TRACEABILITY_MATRIX.md`, and the vault ontology.

This document describes the registry fields used by the advanced autoresearch
pipeline. It is not a claim that every model is production-ready or execution
realistic.

## Implemented Fields

Each model entry may declare:

- `aliases`: natural-language names that map to the canonical model id.
- `default_param_ranges`: deterministic parameter bounds consumed by
  `packages/research_pipeline/parameter_search.py`.
- `recommended_horizon_bars`: documentation and search context for candidate
  holding horizons.
- `valid_instrument_universe`: canonical instruments that the parser marks as
  compatible for that model.
- `volatility_regime`: registry metadata copied into parsed hypothesis receipts.
- `risk_metrics`: expected risk metrics for downstream review and gates.
- `feature_recipe`: only where the registry can state data requirements and
  point-in-time boundaries.

Known parameter aliases:

| Registry key | Candidate key |
|---|---|
| `signal_threshold` | `signal_threshold` |
| `stop_loss` | `stop_loss_pct` |
| `take_profit` | `take_profit_pct` |
| `holding_bars` | `holding_period_bars` |
| `holding_period_bars` | `holding_period_bars` |

## Parser Examples

| Thesis text | Expected model | Notable metadata |
|---|---|---|
| `Run a blowout fade on MES after CPI` | `SPREAD_BLOWOUT_RECOMPRESSION` | model aliases and default ranges from registry |
| `trade micro NQ futures after CPI` | parser universe includes `MNQ` | symbol alias from `symbol_aliases.yaml` |
| `Run a blowout fade on GOLD after CPI` | `SPREAD_BLOWOUT_RECOMPRESSION` | `GC` is recorded as unsupported when outside the model universe |

## Failure Rules

- Unknown model aliases fall back to the existing heuristic parser path.
- Unknown symbols do not become silently compatible.
- Models missing `valid_instrument_universe` are not routable through the CLI;
  candidate generation fails closed until the registry declares compatibility.
- Registry ranges define the candidate search universe before evaluation.
- Bayesian/evolutionary search method requests are currently explicit
  `method_unavailable` fallbacks to deterministic seeded search.
- Registry metadata is research evidence only; promotion still requires the
  normal VectorBT, robustness, HftBacktest realism, and review gates.
