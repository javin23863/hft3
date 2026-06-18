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

## OPEN (this plan — multi-generation loop)

| Capability | Missing connection | Minimal change |
|---|---|---|
| Multi-gen driver | No `--autoresearch` loop | `generation_loop.py` + CLI flags |
| Cross-gen lineage | No `autoresearch_manifest.json` | `generation_state.py` |
| Elite refinement Gen N+1 | No neighbor expansion from validated elites | `elite_refinement.py` |
| Generation outcome aggregation | No validated summary across runners | `generation_summary.py` |
| Workbench robustness from loop | `run_campaign` not called from autoresearch | Wire top-K in `generation_loop.py` |
| Hft campaign from loop | `run_hftbacktest_campaign` not called from autoresearch | Wire after screening in `generation_loop.py` |
| Review memory roots | Wrong workbench scan paths | Fix `_candidate_roots()` |
| Generation memory write | No JSONL facts | `append_generation_memory()` |
| Idea `param_ranges` | Hard-coded in `parsed_from_idea` | Honor idea payload ranges |
| Holdout in learner | Could leak into elite selection | Filter in `generation_summary.py` |

## Out of scope

- `packages/hft3/research/run_autonomous.py` scaffold
- New worker pools, databases, daemons
- PR GrepLoop
