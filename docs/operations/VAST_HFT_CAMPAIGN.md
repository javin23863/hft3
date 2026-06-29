# Vast HftBacktest-Only Campaign Operations

## Binding Rule

Vast HBT runs use the HftBacktest-only campaign identity:

```text
canonical_model_id x HBT-normalized source/event x parameter_hash
```

Do not route HBT eligibility through historical screeners, survivor lists, bar
backtests, or bridge artifacts. Those artifacts may remain diagnostic receipts,
but they do not decide what HBT receives.

Pre-HBT failures are blockers:

- `pipeline_blocker` for adapter, strategy, feature-surface, or compile gaps;
- `data_blocker` for missing or invalid HBT source/snapshot files;
- `authority_missing` for missing product metadata, contract metadata, or
  methodology receipts.

Only completed HBT artifacts plus post-HBT gates can create economic
`reject` / `observe` / `promote` decisions.

## Build The Feature Path

On the Vast host, build the compiled feature extension before running the
campaign. Python fallback warnings mean the host is not ready for the full
campaign.

```bash
cd /root/hft3/repo
python -m pip install -e . pybind11
PYBIND11_CMAKE_DIR="$(python -m pybind11 --cmakedir)"
cmake -S . -B build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DPython3_EXECUTABLE="$(python -c 'import sys; print(sys.executable)')" \
  -Dpybind11_DIR="$PYBIND11_CMAKE_DIR" \
  -Wno-dev
cmake --build build --target hft3_features_cpp hft_feature_golden
export HFT3_FEATURES_CPP_BUILD_DIR=/root/hft3/repo/build
```

Verify import before renting more runtime:

```bash
python - <<'PY'
import hft3_features_cpp
print(hft3_features_cpp.__file__)
print(hasattr(hft3_features_cpp, "FeatureExtractor"))
PY
```

## Prepare The Full Lake

Use the full lake manifest. Do not choose a symbol by preference, and do not
special-case MES as the pipeline.

```bash
cd /root/hft3/repo
python scripts/prepare_hftbacktest_only_from_lake_manifest.py \
  --lake-manifest /data/npz/manifest.json \
  --out-root /data/hbt \
  --summary-out /data/hbt/hbt_prepare_summary.json
```

Product metadata authority comes from
`config/hftbacktest/cme_lake_product_metadata.yaml`. The metadata policy is
`explicit_per_symbol_contract_tick_lot_contract_required`: no inherited
ES-shaped defaults, no symbol-as-contract fallback, and no VIX futures fallback
for `VIX.OPT`. Rows without executable HBT product metadata write blocker
manifests.

## Build Campaign Manifests

Build the canonical model/event manifest from prepared HBT manifests:

```bash
python scripts/build_hftbacktest_only_campaign_manifest.py \
  --campaign-id hbt_full_lake_$(date -u +%Y%m%dT%H%M%SZ) \
  --prepared-root /data/hbt/prepared \
  --out /data/hbt/hbt_full_lake_campaign_manifest.jsonl \
  --summary-out /data/hbt/hbt_full_lake_campaign_summary.json \
  --checkpoint-out /data/hbt/hbt_full_lake_campaign_checkpoint.json
```

The base manifest is the no-cherry-pick campaign universe receipt, not an
immediate HBT execution queue. Its summary must prove `canonical_model_count`,
`prepared_unit_count`, `executable_unit_count`, `blocker_unit_count`,
`expected_base_rows`, `emitted_base_rows`, adapter/authority/applicability
counts, `manual_filter_used=false`, `vectorbt_dependency=false`,
`stage_a_dependency=false`, `screening_artifact_dependency=false`, and
`hbt_jobs_started=0`.

If parameter proposals are declared, expand the campaign surface before the
full run:

```bash
python scripts/build_hftbacktest_only_campaign_manifest.py \
  --campaign-id hbt_full_lake_$(date -u +%Y%m%dT%H%M%SZ) \
  --prepared-root /data/hbt/prepared \
  --out /data/hbt/hbt_full_lake_campaign_manifest.jsonl \
  --summary-out /data/hbt/hbt_full_lake_campaign_summary.json \
  --parameter-sets-json config/hftbacktest/parameter_sets.json \
  --parameter-surface-out /data/hbt/hbt_full_lake_parameter_surface.jsonl \
  --parameter-surface-summary-out /data/hbt/hbt_full_lake_parameter_surface_summary.json
```

If `config/hftbacktest/parameter_sets.json` is missing, record
`pipeline_blocker:parameter_sets_config_missing` in the base manifest summary.
Do not synthesize a search grid inside the builder or runner.

Parameter proposals with `objective_evaluations=0` are deterministic proposal
rows only. They are not adaptive optimizer evidence and cannot rank, reject, or
promote anything before HBT recorder and stats artifacts exist.

## Deterministic Canary Before Execution

Before broad HBT execution, create the first execution manifest deterministically
from manifest order. It must use only rows with:

- `admissibility_status=admissible` / data admissible;
- `adapter_status=available` or `adapter_status=ready`;
- `blocker_code=""` and non-empty `authority_refs`;
- base summary `parameter_surface_status=base_only` or
  `parameter_surface_config_status=parameter_config_present`;
- no manual model, symbol, or instrument preference.

The canary selector is `first N rows after applying those readiness predicates`,
never a hand-picked model or symbol.

Build that canary manifest with the same streaming builder:

```bash
python scripts/build_hftbacktest_only_campaign_manifest.py \
  --campaign-id hbt_full_lake_$(date -u +%Y%m%dT%H%M%SZ) \
  --prepared-root /data/hbt/prepared \
  --out /data/hbt/hbt_full_lake_campaign_manifest.jsonl \
  --summary-out /data/hbt/hbt_full_lake_campaign_summary.json \
  --checkpoint-out /data/hbt/hbt_full_lake_campaign_checkpoint.json \
  --canary-out /data/hbt/hbt_full_lake_canary_manifest.jsonl \
  --canary-count <N> \
  --canary-summary-out /data/hbt/hbt_full_lake_canary_summary.json
```

Then run that canary manifest first:

```bash
python scripts/run_hftbacktest_only_campaign.py \
  --campaign-manifest /data/hbt/hbt_full_lake_canary_manifest.jsonl \
  --out-root /data/hbt/campaign_runs \
  --workers 12 \
  --resume
```

Only after the canary receipts pass may the same execution contract be promoted
to the broader parameter-surface or base-only eligible row set. The full base
universe still remains a manifest/accounting artifact; blocked rows write
blocker receipts and are not model failures.

Tune workers from measured Vast throughput and memory pressure. Worker count is
an HBT runtime decision, not inherited from any historical screening run.

## Required Receipts

Copy these back to the workstation:

```text
/data/hbt/hbt_prepare_summary.json
/data/hbt/hbt_full_lake_campaign_manifest.jsonl
/data/hbt/hbt_full_lake_campaign_summary.json
/data/hbt/hbt_full_lake_campaign_checkpoint.json
/data/hbt/hbt_full_lake_canary_manifest.jsonl
/data/hbt/campaign_runs/**/campaign_row_result.json
/data/hbt/campaign_runs/**/run_manifest.json
/data/hbt/campaign_runs/**/recorder_result.npz
/data/hbt/campaign_runs/**/stats_summary.json
/data/hbt/campaign_runs/**/promotion_decision.json
```

`promotion_decision.json` is valid only after `recorder_result.npz` and
`stats_summary.json` exist for that HBT row.
