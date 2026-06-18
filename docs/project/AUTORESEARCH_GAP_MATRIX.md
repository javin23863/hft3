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

## DONE (Phase 7 — family-aware autoresearch Gen N+1)

| Capability | Location |
|---|---|
| Bounded family recipe variants | `feature_family_proposals.py` |
| Elite refinement + family search budget | `elite_refinement.py`, `config/autoresearch/default.yaml` |
| Elite row carries `feature_recipe` | `generation_summary.py` |

## OPEN (remaining feature-family work)

| Capability | Missing connection | Minimal change |
|---|---|---|
| Cross-asset real leader | Placeholder OFI | Phase 2: multi-symbol sync |
| End-to-end validation smoke | Phase 8 | Full pipeline autoresearch + fs_v1 + HBT handoff |
| Paid-compute readiness | Phase 9 | `FEATURE_FAMILY_STATUS_MANIFEST.yaml` gate |

## DONE (this plan slice — multi-generation loop shell)

| Capability | Location |
|---|---|
| Multi-gen driver | `generation_loop.py` + `--autoresearch` |
| Cross-gen lineage | `generation_state.py`, `autoresearch_manifest.json` |
| Elite refinement Gen N+1 | `elite_refinement.py` (execution params + family recipe variants) |
| Generation outcome aggregation | `generation_summary.py` |
| Workbench robustness from loop | `make_default_robustness_fn` → `frozen_strategy_params` |
| Review memory roots + JSONL | `review_memory.py` |
| Holdout exclusion in summary | `generation_summary.py` |

## Out of scope

- `packages/hft3/research/run_autonomous.py` scaffold
- New worker pools, databases, daemons
- PR GrepLoop
