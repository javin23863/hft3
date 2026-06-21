# Autoresearch three-generation acceptance (2026-06-20)

- **Mode:** fixture_dry_run (no live VectorBT/HftBacktest compute)
- **Exit code:** 0
- **Campaign ID:** `autoresearch_CPI_2024_09_11_TIGHT_20260620T221114Z_3228b4d5`
- **Generations run:** 3
- **Stop reason:** max_generations
- **Deduplication (tested_parameter_hashes):** 17
- **Gen-2 real feature-recipe dimension change:** True

## Generation 0

- Proposed candidates: **4**
- FINAL_PASS: **2**
- Gate rejects by type:
  - ontology: 1
  - vectorbt: 1

## Generation 1

- Proposed candidates: **7**
- FINAL_PASS: **3**
- Gate rejects by type: none

## Generation 2

- Proposed candidates: **7**
- FINAL_PASS: **3**
- Gate rejects by type: none

## Generation 1 parent-child recipe changes

- `fcd8ec436e7c0618` parent=`seed_elite` reason=`exploitation:execution_parameter` recipe_changed=True variant=`None`
- `c7c3c3bb8e12e47f` parent=`seed_elite` reason=`exploitation:execution_parameter` recipe_changed=True variant=`None`
- `486d13ea09ec61a0` parent=`seed_elite` reason=`exploitation:execution_parameter` recipe_changed=True variant=`None`
- `fv_0ff19d5218ce7560` parent=`seed_elite` reason=`family_variant:cross_asset_es_leader` recipe_changed=True variant=`None`
- `fv_8cdee6d72b836dd5` parent=`seed_elite` reason=`family_variant:vix_sensor_declared` recipe_changed=True variant=`None`
- `fv_43fbbfe107dd136c` parent=`seed_elite` reason=`family_variant:macro_context_uplift` recipe_changed=True variant=`None`
- `reject_stat_planted_g1` parent=`None` reason=`generation` recipe_changed=False variant=`None`

## Generation 2 parent-child recipe changes

- `7fa610bc7376eb45` parent=`fcd8ec436e7c0618` reason=`exploitation:execution_parameter` recipe_changed=True variant=`None`
- `61fd0bc21b43e4ec` parent=`fcd8ec436e7c0618` reason=`exploitation:execution_parameter` recipe_changed=True variant=`None`
- `dac4aa2c0829715d` parent=`fcd8ec436e7c0618` reason=`exploitation:execution_parameter` recipe_changed=True variant=`None`
- `fv_f634cb6fc5f56e4c` parent=`fcd8ec436e7c0618` reason=`family_variant:cross_asset_es_leader` recipe_changed=True variant=`None`
- `fv_813d4c09cdfebe5e` parent=`fcd8ec436e7c0618` reason=`family_variant:vix_sensor_declared` recipe_changed=True variant=`None`
- `fv_3728e295ea49d2bd` parent=`fcd8ec436e7c0618` reason=`family_variant:macro_context_uplift` recipe_changed=True variant=`None`
- `reject_stat_planted_g2` parent=`None` reason=`generation` recipe_changed=False variant=`None`

## Blockers / honesty

This acceptance run uses planted fake runners and minimal NPZ fixtures.
It does **not** certify live paid-screen throughput or CHI404 replay latency.
