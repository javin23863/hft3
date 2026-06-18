# Autoresearch Gap Matrix (post–VectorBT merge)

Status as of merge `d6665025` (VectorBT VBT-HBT handoff + HftBacktest campaign on `main`).

## DONE (VectorBT / handoff — do not re-implement)

| Capability | Location |
|---|---|
| Terminal `screening_artifact.json` | `vectorbt_adapter.persist_screening_artifact` + `run_pipeline.py` |
| Screening hash + feature plane validation | `vectorbt_adapter.validate_screening_artifact`, `hft_campaign/validation.py` |
| VBT-3 surface stability, VBT-4 robustness bridge | `surface_stability.py`, `robustness_bridge.py` |
| Single-shot `--hftbacktest-realism` handoff | `run_pipeline.py`, `hftbacktest_realism.py` |
| HftBacktest campaign runner + manifest | `hft_campaign/runner.py`, `manifest.py` |
| Workbench robustness / WFC | `campaign_runner.run_campaign` |

## OPEN (superseded by FEATURE_FAMILY_IMPLEMENTATION_AUDIT — Phase 7)

| Capability | Missing connection | Minimal change |
|---|---|---|
| Feature-recipe candidate generation | Autoresearch refines 4 execution params only | Phase 7: family-aware `propose_next_candidates` |
| VectorBT fs_v1 row-loop consumption | Implemented when feature store NPZ exists; bar stub fallback otherwise | Phase 6: HBT recipe-hash equality gate |
| HBT recipe-hash equality | No cross-check yet | Phase 6: gate in handoff |
| Cross-asset real leader | Placeholder OFI | Phase 2: multi-symbol sync |

## DONE (this plan slice — multi-generation loop shell)

| Capability | Location |
|---|---|
| Multi-gen driver | `generation_loop.py` + `--autoresearch` |
| Cross-gen lineage | `generation_state.py`, `autoresearch_manifest.json` |
| Elite refinement Gen N+1 | `elite_refinement.py` (execution params; family recipes Phase 7) |
| Generation outcome aggregation | `generation_summary.py` |
| Workbench robustness from loop | `make_default_robustness_fn` → `frozen_strategy_params` |
| Review memory roots + JSONL | `review_memory.py` |
| Holdout exclusion in summary | `generation_summary.py` |

## Out of scope

- `packages/hft3/research/run_autonomous.py` scaffold
- New worker pools, databases, daemons
- PR GrepLoop
