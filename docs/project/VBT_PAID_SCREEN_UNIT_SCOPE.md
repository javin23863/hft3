# VectorBT paid-screen unit scope (authoritative)

Status: binding scope for Phase D full rent. Supersedes ad-hoc CPI+NFP / single-hypothesis JSONL.

Authority: [OPPORTUNITY_RESEARCH_SPEC.md](OPPORTUNITY_RESEARCH_SPEC.md), [CME_M6_SWEEP_CONTROL_PLAN.md](../cockpit/CME_M6_SWEEP_CONTROL_PLAN.md) (symbol universe), [VBT_PAID_SCREEN_RUNBOOK.md](VBT_PAID_SCREEN_RUNBOOK.md), [VBT_MODEL_ONTOLOGY.md](VBT_MODEL_ONTOLOGY.md).

## Wrong scope (do not use for full rent)

| Anti-pattern | Why wrong |
|--------------|-----------|
| `--event-types CPI,NFP` only | Drops most macro/event families in the catalog |
| `--model-id HYP_5` only | One hypothesis; full rent needs all active models |
| `[:50]` event cap per type | Under-counts; full expansion must use **all** TIGHT events for each eligible row |
| `run_event_universe` as discovery | Retired broad-discovery path; VectorBT screening artifact first |
| Local `stage_a_survivors.json` as Phase D prerequisite | Stage A is a separate M6 job; VectorBT full screen generates units from events + model registry on Vast |

Phase B smoke may use 8–16 diverse units (including CPI/NFP) — that is **not** the full unit manifest.

## Correct scope (Phase D1 — on Vast host)

**Source:** `packages/data_system/config/events.csv` + active hypothesis registry (`get_active_hypotheses()` → canonical model slugs).

**Generation (default):**

```bash
python scripts/generate_vbt_paid_units_jsonl.py \
  --all-active-models \
  --research-split discovery_confirmation \
  --events-csv packages/data_system/config/events.csv \
  --symbols MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0 \
  --out runtime/reports/vbt_full_units.jsonl
```

**Walk-forward / holdout protection (BLUEPRINT §8):** Default `--research-split discovery_confirmation` includes Discovery (2018–2020) and Confirmation (2021–2022) only. Holdout (2023–2024) and Recent holdout (2025) are **excluded** unless you pass `--research-split holdout`, `--research-split recent_holdout`, or `--research-split all` explicitly. Each unit carries `research_split` metadata. Override with `--start-date` / `--end-date` when owner narrows scope.

Or one-shot via `bash scripts/run_vbt_paid_screen_vast_full.sh` (generates units then runs orchestrator; requires matching `runtime/reports/vbt_full_run_declaration.json`).

**Event filter:** `window_name=TIGHT`, every row whose `symbols` column intersects the symbol filter. Optional `VBT_EVENT_TYPES` / `--event-types` narrows families.

**Model filter:** all active hypotheses by default (`VBT_MODEL_SCOPE=active`). Optional `VBT_MODEL_IDS` / `--model-ids` for owner-narrowed reruns.

**Symbols (default):** `MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0` — same as CME M6 sweep control plan. Not MES-only unless owner narrows scope.

**One VectorBT work unit** = one `(model_id=<canonical slug>, symbol, event_id)` screening run via `run_pipeline.py --vectorbt --vectorbt-scope paid-compute`. See [VBT_MODEL_ONTOLOGY.md](VBT_MODEL_ONTOLOGY.md) for model composition (fs_v1 slots, gates, params).

This is **not** the M6 universe unit (which runs all active hypotheses inside one NPZ×latency replay). VectorBT paid units are **per hypothesis** per event×symbol.

## Historical reference (Stage A survivors — not VectorBT full default)

Stage A (`scripts/run_stage_a_screen.py` → `stage_a_survivors.json`) was used for M6 `run_event_universe --from-stage-a` cell filtering. That path remains available for backward compatibility:

```bash
python scripts/generate_vbt_paid_units_jsonl.py \
  --from-stage-a-survivors research_cards/stage_a_full/stage_a_survivors.json \
  --symbols MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0 \
  --out runtime/reports/vbt_full_units.jsonl
```

Do **not** treat survivor count (423) as `expected_work_units` for VectorBT full scope.

## Expected work-unit count

```bash
wc -l runtime/reports/vbt_full_units.jsonl
```

Record that line count in `runtime/reports/vbt_full_run_declaration.json` before rent (on Vast after on-host generation). `run_vbt_paid_screen_vast_full.sh` **refuses to start workers** if the declaration is missing or `expected_work_units` does not match the generated JSONL line count.

Historical reference (different job shape): M6 `run_event_universe` full scope reported ~28,136 JSONL rows with one latency band and all hypotheses inside each unit — not comparable 1:1 to VectorBT per-hypothesis units.

## Downstream HftBacktest realism

HftBacktest is the heavier downstream realism pass on **VectorBT promoted outputs** — not a prerequisite to unit generation or Vast full rent.

## Vast workers

On 256 vCPU hosts: **≥230 workers** (`run_vectorbt_paid_screen.py --workers 230`). Do not run full rent at 4 workers — that is smoke-only topology.

```bash
bash scripts/run_vbt_paid_screen_vast_full.sh
```

## Data on Vast

NPZ lake must already be on the instance (`HFT3_NPZ_ROOT`, `HFT3_MANIFEST_PATH`). Do not re-download if yesterday's lake is present; verify hash matches gate pilot/smoke before starting workers.
