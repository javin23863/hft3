# VectorBT paid-screen unit scope (authoritative)

Status: binding scope for Phase D full rent. Supersedes ad-hoc CPI+NFP / single-hypothesis JSONL.

Authority: [OPPORTUNITY_RESEARCH_SPEC.md](OPPORTUNITY_RESEARCH_SPEC.md) (Stage A 423 survivors, 16,931 Stage A units), [CME_M6_SWEEP_CONTROL_PLAN.md](../cockpit/CME_M6_SWEEP_CONTROL_PLAN.md) (symbol universe), [VBT_PAID_SCREEN_RUNBOOK.md](VBT_PAID_SCREEN_RUNBOOK.md).

## Wrong scope (do not use for full rent)

| Anti-pattern | Why wrong |
|--------------|-----------|
| `--event-types CPI,NFP` only | Drops most macro/event families Stage A tested |
| `--model-id HYP_5` only | One hypothesis; Stage A has **423 survivor cells** across many `hyp_id` × `event_type` pairs |
| `[:50]` event cap per type | Under-counts; full expansion must use **all** TIGHT events for each allowed cell |
| `run_event_universe` as discovery | Retired broad-discovery path; VectorBT screening artifact first |

Phase B smoke may use 8–16 diverse units (including CPI/NFP) — that is **not** the full unit manifest.

## Correct scope (Phase D1)

**Source:** `research_cards/stage_a_full/stage_a_survivors.json` (from `scripts/run_stage_a_screen.py` full run).

**Cell filter (mirrors `run_event_universe.py --from-stage-a`):**

1. Every `(hyp_id, event_type)` in `survivors`.
2. Every `pass_through` `hyp_id` expanded across all `event_type` values in `tested_cells`.
3. Optional `--cells` overrides (same as M6).

**Symbols (default):** `MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0` — same as CME M6 sweep control plan. Not MES-only unless owner narrows scope.

**Events:** `packages/data_system/config/events.csv`, `window_name=TIGHT`, every row whose `event_type` is in the allowed set and whose `symbols` column intersects the symbol filter.

**One VectorBT work unit** = one `(model_id=HYP_{hyp_id}, symbol, event_id)` screening run via `run_pipeline.py --vectorbt --vectorbt-scope paid-compute`.

This is **not** the M6 universe unit (which runs all active hypotheses inside one NPZ×latency replay). VectorBT paid units are **per hypothesis** per event×symbol.

## Expected work-unit count

Do not use survivor count (423) as `expected_work_units`.

```bash
python scripts/generate_vbt_paid_units_jsonl.py \
  --from-stage-a-survivors research_cards/stage_a_full/stage_a_survivors.json \
  --symbols MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0 \
  --out runtime/reports/vbt_full_units.jsonl

wc -l runtime/reports/vbt_full_units.jsonl
```

Record that line count in `runtime/reports/vbt_full_run_declaration.json` before rent.

Historical reference (different job shape): M6 `run_event_universe` full scope reported ~28,136 JSONL rows with one latency band and all hypotheses inside each unit — not comparable 1:1 to VectorBT per-hypothesis units.

## Vast workers

On 256 vCPU hosts: **≥230 workers** (`run_vectorbt_paid_screen.py --workers 230`). Do not run full rent at 4 workers — that is smoke-only topology.

```bash
bash scripts/run_vbt_paid_screen_vast_full.sh
```

## Data on Vast

NPZ lake must already be on the instance (`HFT3_NPZ_ROOT`, `HFT3_MANIFEST_PATH`). Do not re-download if yesterday's lake is present; verify hash matches gate pilot/smoke before starting workers.
