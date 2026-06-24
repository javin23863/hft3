# VectorBT paid-screen unit scope (authoritative)

Status: binding scope for Phase D full rent. Supersedes ad-hoc CPI+NFP / single-hypothesis JSONL.

Authority: [OPPORTUNITY_RESEARCH_SPEC.md](OPPORTUNITY_RESEARCH_SPEC.md), [CME_M6_SWEEP_CONTROL_PLAN.md](../cockpit/CME_M6_SWEEP_CONTROL_PLAN.md) (symbol universe), [VBT_PAID_SCREEN_RUNBOOK.md](VBT_PAID_SCREEN_RUNBOOK.md), [VBT_MODEL_ONTOLOGY.md](VBT_MODEL_ONTOLOGY.md).

## Wrong scope (do not use for full rent)

| Anti-pattern | Why wrong |
|--------------|-----------|
| `--event-types CPI,NFP` only | Drops most macro/event families in the catalog |
| `--model-id HYP_5` only | One hypothesis; current full rent needs the Stage-A survivor cells |
| `[:50]` event cap per type | Under-counts; full expansion must use **all** TIGHT events for each eligible row |
| `run_event_universe` as discovery | Retired broad-discovery path; VectorBT screening artifact first |
| Rerunning Stage A during Phase D rent | Stage A is already completed; VectorBT full consumes the survivor file as input on Vast |

Phase B smoke may use 8ΓÇô16 diverse units (including CPI/NFP) ΓÇö that is **not** the full unit manifest.

## Correct scope (Phase D1 ΓÇö on Vast host)

**Source:** `research_cards/stage_a_full/stage_a_survivors.json` + `packages/data_system/config/events.csv` + CME M6 symbol universe, then runnable-NPZ filtering on the Vast host.

**Generation (default):**

```bash
python scripts/generate_vbt_paid_units_jsonl.py \
  --from-stage-a-survivors research_cards/stage_a_full/stage_a_survivors.json \
  --events-csv packages/data_system/config/events.csv \
  --symbols MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0 \
  --require-runnable-npz \
  --research-split discovery_confirmation \
  --out runtime/reports/vbt_full_units.jsonl
```

**Walk-forward / holdout protection (BLUEPRINT section 8):** Current survivor-scoped Vast default uses `VBT_RESEARCH_SPLIT=discovery_confirmation`, so generated units include Discovery (2018-2020) and Confirmation (2021-2022) only. Holdout (2023-2024) and Recent holdout (2025+) require an explicit `VBT_RESEARCH_SPLIT=holdout`, `recent_holdout`, or `all`, or the matching `--research-split` value in a manual generator run.

Or one-shot via `bash scripts/run_vbt_paid_screen_vast_full.sh` (generates units then runs orchestrator; requires matching `runtime/reports/vbt_full_run_declaration.json`).

**Event filter:** `window_name=TIGHT`, every row whose `symbols` column intersects the symbol filter. Optional `VBT_EVENT_TYPES` / `--event-types` narrows families.

**Model filter:** Stage-A survivor cells by default. `VBT_UNIT_SOURCE=all_active` with `VBT_MODEL_SCOPE` / `VBT_MODEL_IDS` is an explicit override for owner-approved exploratory reruns.

**Symbols (default):** `MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0` ΓÇö same as CME M6 sweep control plan. Not MES-only unless owner narrows scope.

**One VectorBT work unit** = one `(model_id=<canonical slug>, symbol, event_id)` screening run via `run_pipeline.py --vectorbt --vectorbt-scope paid-compute`. See [VBT_MODEL_ONTOLOGY.md](VBT_MODEL_ONTOLOGY.md) for model composition (fs_v1 slots, gates, params).

This is **not** the M6 universe unit (which runs all active hypotheses inside one NPZ├ùlatency replay). VectorBT paid units are **per hypothesis** per event├ùsymbol.

## Explicit override reference (all-active, not current default)

All-active expansion remains available when the owner explicitly wants to compare against survivor-scoped filtering:

```bash
python scripts/generate_vbt_paid_units_jsonl.py \
  --all-active-models \
  --research-split discovery_confirmation \
  --symbols MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0 \
  --out runtime/reports/vbt_full_units.jsonl
```

Do **not** treat survivor count (423) as `expected_work_units` for VectorBT full scope.

## Expected work-unit count

```bash
wc -l runtime/reports/vbt_full_units.jsonl
```

Record that line count and the matching git/hash/worker/abort fields in `runtime/reports/vbt_full_run_declaration.json` before rent (on Vast after on-host generation). `run_vbt_paid_screen_vast_full.sh` **refuses to start workers** if the declaration is missing or any approved declaration field does not match the generated JSONL, current git head, events hash, lake manifest hash, worker count, or abort policy.

Historical reference (different job shape): M6 `run_event_universe` full scope reported ~28,136 JSONL rows with one latency band and all hypotheses inside each unit ΓÇö not comparable 1:1 to VectorBT per-hypothesis units.

## Downstream HftBacktest realism

HftBacktest is the heavier downstream realism pass on **VectorBT promoted outputs** ΓÇö not a prerequisite to unit generation or Vast full rent.

## Vast workers

On 256 vCPU hosts: **>=230 workers** via `run_vbt_paid_screen_vast_full.sh` or the documented `run_vectorbt_paid_screen_v2.py` manual equivalent with abort/hash args. Do not run full rent at 4 workers; that is smoke-only topology.

```bash
bash scripts/run_vbt_paid_screen_vast_full.sh
```

## Data on Vast

NPZ lake must already be on the instance (`HFT3_NPZ_ROOT`, `HFT3_MANIFEST_PATH`). Do not re-download if yesterday's lake is present; verify hash matches gate pilot/smoke before starting workers.
