# Paid-Screen Redesign — Operational Commands

Status: **Historical / inactive for active HftBacktest-only routing.**
Operational reference with example commands for every legacy operational
scenario of the v2 paid-screen path
(`scripts/run_vectorbt_paid_screen_v2.py` and the redesigned execution model).

These commands assume:
- Working directory is the repo root (`$HFT3_REPO`).
- `vectorbt[rust]` is installed for `paid-compute` scope.
- `HFT3_NPZ_ROOT` and `HFT3_MANIFEST_PATH` are set and point at the same lake
  used by the pilot/smoke baseline.
- Python is invoked as `python` (Python 3.11).

Related documents:
- `PAID_SCREEN_REDESIGN_DESIGN.md` — architecture and invariants.
- `PAID_SCREEN_MIGRATION_PLAN.md` — migration and rollback plan.
- `PAID_SCREEN_BATCHING_KEY_SPEC.md` — `BatchingKey` contract.
- `PAID_SCREEN_CACHE_SPEC.md` — cache layers and keys.
- `VBT_PAID_SCREEN_RUNBOOK.md` — phase map (pilot → smoke → gate → full →
  completion) and error→action matrix.

---

## Conventions

| Placeholder | Meaning |
|-------------|---------|
| `$VBT_PAID_RUN_ID` | Timestamped run identifier, e.g. `paid_smoke_$(date -u +%Y%m%dT%H%M%SZ)`. |
| `$UNITS` | Path to the JSONL unit manifest. |
| `$OUT` | Run output directory under `research_cards/pipeline_runs/`. |
| `$GATE` | Path to `paid_screen_ready_gate.json`. |
| `$WORKERS` | Worker count. Smoke: 4–8. Workstation: 1–16. Vast 256 vCPU: ≥230. |

All v2 commands use `scripts/run_paid_screen.py` (canonical shim) or
`scripts/run_vectorbt_paid_screen_v2.py` directly. The v1 subprocess-per-unit
orchestrator is **retired**; use archived manifests for historical v1 parity
only.

---

## 1. Smoke run (v2)

**Purpose:** prove the v2 plumbing end-to-end on 8–16 diverse units before any
larger run. Same code path as full rent, small topology.

### 1.1 Generate smoke units

```bash
python scripts/generate_vbt_paid_units_jsonl.py \
  --out runtime/reports/vbt_smoke_units.jsonl \
  --smoke-count 12 \
  --symbols MES.v.0,ES.v.0 \
  --event-types CPI,NFP \
  --model-id HYP_5
```

### 1.2 Run the v2 smoke orchestrator

```bash
export VBT_PAID_RUN_ID="paid_smoke_$(date -u +%Y%m%dT%H%M%SZ)"
export OUT="research_cards/pipeline_runs/${VBT_PAID_RUN_ID}"

python scripts/run_vectorbt_paid_screen_v2.py \
  --units-jsonl runtime/reports/vbt_smoke_units.jsonl \
  --out "$OUT" \
  --vectorbt-scope paid-compute \
  --workers 4 \
  --max-wall-clock-seconds 3600 \
  --no-llm
```

> **Ready-gate note:** `--workers > 1` without `--dry-run` requires either
> `--ready-gate-file` or `--owner-waiver`. For a first smoke on the
> workstation, use `--workers 1` to bypass the gate, or pass
> `--owner-waiver "initial v2 smoke, gate pending"` with a reason string.

### 1.3 Smoke pass checklist

| Check | Command / field |
|-------|-----------------|
| Manifest exists | `ls "$OUT/paid_screen_run_manifest.json"` |
| Orchestrator version | `"orchestrator_version": "v2"` in the manifest |
| Unit count invariant | `completed + skipped + failed == expected` |
| Zero failures | `failed_work_units == 0` |
| Per-unit artifacts | every `OK` unit: `validate_screening_artifact` passes on `units/<unit_id>/screening_artifact.json` |
| Engine | `vectorbt_engine == "rust"` for `paid-compute` scope |
| Throughput | record `units_per_hour` from the manifest |

Validate a single artifact:

```bash
python -c "
import json, sys
from backtest_pipeline.src.vectorbt_adapter import validate_screening_artifact
payload = json.loads(open(sys.argv[1]).read())
validate_screening_artifact(payload)
print('OK')
" "$OUT/units/<unit_id>/screening_artifact.json"
```

---

## 2. Parity run (old vs new)

**Purpose:** prove v2 produces the same promoted/rejected IDs and artifact
schema as v1 on the **same** unit corpus and hashes. This is the core
migration gate (see `PAID_SCREEN_MIGRATION_PLAN.md` §2).

### 2.1 v1 baseline (retired — historical reference only)

> **Retired 2026-06:** the v1 paid-screen runner was deleted. Use
> archived v1 manifests under `runtime/reports/paid_screen_v1_baseline/` for
> parity comparison; do not attempt to re-run v1.

```bash
# Historical — v1 script no longer exists.
```

Record the `events_csv_hash` and `lake_manifest_hash` from the v1 manifest
(needed to make v2 cache keys directly comparable):

```bash
python -c "
import json
m = json.loads(open('$VBT_V1_DIR/paid_screen_run_manifest.json').read())
print('events_csv_hash=', m.get('events_csv_hash', 'n/a'))
print('lake_manifest_hash=', m.get('lake_manifest_hash', 'n/a'))
"
```

> If the v1 manifest does not surface the hashes explicitly, read them from a
> per-unit artifact's `events_csv_hash` / `lake_manifest_hash` fields, or pass
> them explicitly to both orchestrators via the `--events-csv-hash` /
> `--lake-manifest-hash` flags (v2) and the environment (v1 reads them from the
> pipeline).

### 2.2 v2 parity run with matching hashes

```bash
export VBT_V2_DIR="runtime/reports/paid_screen_v2_parity"

python scripts/run_vectorbt_paid_screen_v2.py \
  --units-jsonl runtime/reports/vbt_smoke_units.jsonl \
  --out "$VBT_V2_DIR" \
  --vectorbt-scope paid-compute \
  --workers 1 \
  --max-wall-clock-seconds 3600 \
  --no-llm \
  --events-csv-hash  "<v1 events_csv_hash>" \
  --lake-manifest-hash "<v1 lake_manifest_hash>" \
  --resume
```

### 2.3 Compare manifests

```bash
python - <<'PY'
import json, sys

v1 = json.loads(open("runtime/reports/paid_screen_v1_baseline/paid_screen_run_manifest.json").read())
v2 = json.loads(open("runtime/reports/paid_screen_v2_parity/paid_screen_run_manifest.json").read())

assert v1["expected_work_units"] == v2["expected_work_units"], "expected mismatch"

r1 = {r["unit_id"]: r for r in v1["unit_results"]}
r2 = {r["unit_id"]: r for r in v2["unit_results"]}
assert set(r1) == set(r2), "unit-id set mismatch"

mismatches = []
for uid in r1:
    s1, s2 = r1[uid]["status"], r2[uid]["status"]
    p1 = sorted(r1[uid].get("promoted_ids", []))
    p2 = sorted(r2[uid].get("promoted_ids", []))
    q1 = sorted(r1[uid].get("rejected_ids", []))
    q2 = sorted(r2[uid].get("rejected_ids", []))
    if s1 != s2 or p1 != p2 or q1 != q2:
        mismatches.append((uid, s1, s2, p1, p2, q1, q2))

if mismatches:
    for m in mismatches:
        print("MISMATCH", *m)
    sys.exit(1)
print("PARITY OK: %d units, all statuses and promoted/rejected IDs match" % len(r1))
PY
```

**Pass criterion:** `PARITY OK` with zero mismatches. Any mismatch is a
correctness bug — do not proceed to full rent; file in the migration log and
re-run the `test_paid_screen_matrix.py` parity assertions.

---

## 3. Benchmark run

**Purpose:** measure v2 throughput (`units_per_hour`) and cache hit rate versus
v1 at the same worker count, to confirm the redesign's amortization benefit.

### 3.1 v2 benchmark (single worker, for a clean comparison)

```bash
export VBT_BENCH_DIR="research_cards/pipeline_runs/paid_bench_$(date -u +%Y%m%dT%H%M%SZ)"

python scripts/run_vectorbt_paid_screen_v2.py \
  --units-jsonl runtime/reports/vbt_smoke_units.jsonl \
  --out "$VBT_BENCH_DIR" \
  --vectorbt-scope paid-compute \
  --workers 1 \
  --max-wall-clock-seconds 3600 \
  --no-llm
```

### 3.2 v1 benchmark (retired — historical reference only)

> **Retired 2026-06:** v1 orchestrator deleted. Compare against archived v1
> benchmark manifests only.

```bash
# Historical — script no longer exists:
# use archived v1 benchmark manifests only.
```

### 3.3 Compare throughput

```bash
python - <<'PY'
import json
v1 = json.loads(open("research_cards/pipeline_runs/paid_bench_v1_<ts>/paid_screen_run_manifest.json").read())
v2 = json.loads(open("research_cards/pipeline_runs/paid_bench_<ts>/paid_screen_run_manifest.json").read())
print("v1 units_per_hour:", v1["units_per_hour"])
print("v2 units_per_hour:", v2["units_per_hour"])
print("v2 cache hits/misses:",
      sum(s.get("cache_hits", 0) for s in v2.get("worker_profiler_summaries", [])),
      "/", sum(s.get("cache_misses", 0) for s in v2.get("worker_profiler_summaries", [])))
PY
```

**Expected:** v2 `units_per_hour` ≥ v1 at `--workers 1` (v2 amortizes imports
and VectorBT/Rust init across batches; v1 spawns a subprocess per unit). At
higher worker counts the gap widens. Record both numbers in the migration log.

### 3.4 Scale benchmark (optional, on a larger host)

```bash
python scripts/run_vectorbt_paid_screen_v2.py \
  --units-jsonl runtime/reports/vbt_full_units.jsonl \
  --out "research_cards/pipeline_runs/paid_bench_scale_$(date -u +%Y%m%dT%H%M%SZ)" \
  --vectorbt-scope paid-compute \
  --workers 16 \
  --max-wall-clock-seconds 7200 \
  --ready-gate-file runtime/reports/paid_screen_ready_gate.json \
  --max-batches-before-recycle 100 \
  --cache-memory-limit-mb 4096 \
  --cache-max-entries 1000 \
  --no-llm
```

Tune `--max-batches-before-recycle`, `--cache-memory-limit-mb`, and
`--cache-max-entries` based on observed memory and hit rate (see
`PAID_SCREEN_CACHE_SPEC.md` §5 for eviction semantics).

---

## 4. Resumable production run

**Purpose:** execute the full paid screen on Vast (256 vCPU) with resume
support so an interruption does not lose completed units.

### 4.1 Full unit manifest (Stage A scope)

```bash
python scripts/generate_vbt_paid_units_jsonl.py \
  --from-stage-a-survivors research_cards/stage_a_full/stage_a_survivors.json \
  --events-csv packages/data_system/config/events.csv \
  --symbols MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0 \
  --out runtime/reports/vbt_full_units.jsonl

wc -l runtime/reports/vbt_full_units.jsonl
```

Record the line count as `expected_work_units` in
`runtime/reports/vbt_full_run_declaration.json` before rent.

### 4.2 Initial full run (Vast, tmux)

```bash
export VBT_FULL_RUN_ID="paid_full_$(date -u +%Y%m%dT%H%M%SZ)"
export OUT="research_cards/pipeline_runs/${VBT_FULL_RUN_ID}"

python scripts/run_vectorbt_paid_screen_v2.py \
  --units-jsonl runtime/reports/vbt_full_units.jsonl \
  --out "$OUT" \
  --vectorbt-scope paid-compute \
  --workers 230 \
  --ready-gate-file runtime/reports/paid_screen_ready_gate.json \
  --max-wall-clock-seconds 86400 \
  --max-batches-before-recycle 100 \
  --cache-memory-limit-mb 4096 \
  --cache-max-entries 1000 \
  --batch-timeout-seconds 1800 \
  --no-llm
```

> `--workers > 1` **requires** `--ready-gate-file` (or `--owner-waiver` with a
> reason string). The gate must report `ready_for_full_run: true`.

### 4.3 Resume after interruption

If the run is interrupted (crash, stall-kill, budget exhaustion, manual
stop), resume it. `--resume` skips units whose
`units/<unit_id>/screening_artifact.json` already **validates**; invalid or
missing artifacts are recomputed (Phase 6 hardening: valid-skip /
invalid-recompute).

```bash
python scripts/run_vectorbt_paid_screen_v2.py \
  --units-jsonl runtime/reports/vbt_full_units.jsonl \
  --out "$OUT" \
  --vectorbt-scope paid-compute \
  --workers 230 \
  --ready-gate-file runtime/reports/paid_screen_ready_gate.json \
  --max-wall-clock-seconds 86400 \
  --no-llm \
  --resume
```

The resumed manifest records `skipped_unit_ids` (the valid-artifact units that
were skipped) and `skipped_work_units` in the count invariant.

### 4.4 Stall monitor (supervisor shell)

While the full run is in progress, poll for progress every 5 minutes:

```bash
python scripts/validate_paid_screen_ready_gate.py \
  --watch-manifest "$OUT/paid_screen_run_manifest.json" \
  --stall-minutes 30
```

If stalled (no progress for 30 min): capture `ps`, `iostat`, last 20 log lines;
kill the worker pool; preserve the run directory; resume with `--resume` only
if the stall cause is resolved. Do **not** import partial results as GREEN.

---

## 5. Artifact validation

**Purpose:** confirm every per-unit screening artifact is schema-valid and
carries the no-lookahead proof, for both smoke and full runs.

### 5.1 Validate a single artifact

```bash
python -c "
import json, sys
from backtest_pipeline.src.vectorbt_adapter import validate_screening_artifact
p = json.loads(open(sys.argv[1]).read())
validate_screening_artifact(p)
assert p.get('no_lookahead_signal_shift_proof'), 'missing no_lookahead proof'
assert p.get('vectorbt_engine') == 'rust', 'engine not rust'
print('OK')
" "$OUT/units/<unit_id>/screening_artifact.json"
```

### 5.2 Validate all artifacts in a run (batch)

```bash
python - <<'PY'
import json, sys
from pathlib import Path
from backtest_pipeline.src.vectorbt_adapter import validate_screening_artifact

run_dir = Path(sys.argv[1])
manifest = json.loads((run_dir / "paid_screen_run_manifest.json").read_text())
ok = fail = 0
for row in manifest["unit_results"]:
    if row["status"] not in ("OK", "OK_CACHED"):
        continue
    p = run_dir / f"units/{row['unit_id']}/screening_artifact.json"
    try:
        payload = json.loads(p.read_text())
        validate_screening_artifact(payload)
        assert payload.get("no_lookahead_signal_shift_proof"), "no_lookahead missing"
        assert payload.get("vectorbt_engine") == "rust", "engine not rust"
        ok += 1
    except Exception as e:
        print("FAIL", row["unit_id"], e)
        fail += 1
print(f"validated={ok} failed={fail}")
sys.exit(1 if fail else 0)
PY "$OUT"
```

### 5.3 Manifest honesty check

```bash
python - <<'PY'
import json, sys
m = json.loads(open(sys.argv[1]).read())
c, s, f, e = (m["completed_work_units"], m["skipped_work_units"],
              m["failed_work_units"], m["expected_work_units"])
assert c + s + f == e, f"count invariant broken: {c}+{s}+{f} != {e}"
assert m["status"] == "complete", f"status not complete: {m['status']}"
assert m["orchestrator_version"] == "v2", "not v2"
assert f == 0 or m.get("failure_diagnostics_path"), "failures without diagnostics"
print("MANIFEST OK", dict(completed=c, skipped=s, failed=f, expected=e))
PY "$OUT/paid_screen_run_manifest.json"
```

### 5.4 Import-quarantine sample (post-run, before cockpit)

Copy manifest + units to the workstation and validate a random sample of at
least 10 units before any cockpit aggregation:

```bash
python - <<'PY'
import json, random, sys
from pathlib import Path
from backtest_pipeline.src.vectorbt_adapter import validate_screening_artifact

run_dir = Path(sys.argv[1])
m = json.loads((run_dir / "paid_screen_run_manifest.json").read_text())
ok = [r for r in m["unit_results"] if r["status"] in ("OK", "OK_CACHED")]
sample = random.sample(ok, min(10, len(ok)))
for r in sample:
    p = run_dir / f"units/{r['unit_id']}/screening_artifact.json"
    validate_screening_artifact(json.loads(p.read_text()))
print(f"quarantine sample OK: {len(sample)} units validated")
PY "$OUT"
```

---

## 6. Dry run

**Purpose:** print the execution plan (unit count, batch count, worker count)
without executing any screening. Useful for validating a unit manifest before
rent and for capacity planning.

### 6.1 v2 dry run

```bash
python scripts/run_vectorbt_paid_screen_v2.py \
  --units-jsonl runtime/reports/vbt_full_units.jsonl \
  --out "research_cards/pipeline_runs/dry_run_preview" \
  --vectorbt-scope paid-compute \
  --workers 230 \
  --dry-run
```

Output (example):

```
DRY_RUN units=28136 after_resume=28136 batches=4127 workers=230 scope=paid-compute out=research_cards/pipeline_runs/dry_run_preview
{"unit_id": "...", "model_id": "HYP_5", "symbol": "MES.v.0", "event_id": "CPI_2024_09_11_TIGHT", "event_type": "CPI"}
...
```

Key fields to verify:
- `units` == `wc -l` of the JSONL.
- `batches` is the number of `(symbol, event_id)` groups — fewer than `units`
  is expected and correct (batching collapses units sharing an event).
- `after_resume` is `units` without `--resume`. With `--dry-run --resume`,
  hashes are resolved and valid resume artifacts are scanned before grouping,
  so `after_resume` is the remaining unit count after resume filtering.

### 6.2 Dry run with resume preview

```bash
python scripts/run_vectorbt_paid_screen_v2.py \
  --units-jsonl runtime/reports/vbt_full_units.jsonl \
  --out "$OUT" \
  --vectorbt-scope paid-compute \
  --workers 230 \
  --dry-run \
  --resume
```

`after_resume` is the post-resume remaining unit count here. A value below
`units` means existing artifacts validated and were skipped.

---

## 7. Ready gate enforcement

**Purpose:** the fail-closed gate that blocks full rent until pilot + smoke
pass. Produced by `scripts/validate_paid_screen_ready_gate.py`.

### 7.1 Evaluate the gate (after pilot + smoke)

```bash
python scripts/validate_paid_screen_ready_gate.py \
  --pilot-artifact "$VBT_PILOT_ARTIFACT" \
  --smoke-manifest "$VBT_SMOKE_MANIFEST" \
  --out runtime/reports/paid_screen_ready_gate.json
```

- **Exit 0** → `ready_for_full_run: true` written; full rent may proceed.
- **Exit 1** → `ready_for_full_run: false` written with an `errors` list; do not
  rent. Fix each error and re-evaluate.

The gate fails on:
- Missing or invalid pilot/smoke artifacts.
- `failed_work_units > 0` in the smoke manifest.
- `completed + skipped + failed != expected` in the smoke manifest.
- Hash mismatch (`events_csv_hash`, `lake_manifest_hash`) between pilot and
  smoke.
- `paid-compute` scope units without `vectorbt_engine == "rust"`.
- Missing `no_lookahead_signal_shift_proof` on any validated artifact.
- Lookahead pytest failure (run by default; skip with `--skip-pytest` only with
  a documented reason).

### 7.2 Use the gate in a full run

```bash
python scripts/run_vectorbt_paid_screen_v2.py \
  --units-jsonl runtime/reports/vbt_full_units.jsonl \
  --out "$OUT" \
  --vectorbt-scope paid-compute \
  --workers 230 \
  --ready-gate-file runtime/reports/paid_screen_ready_gate.json \
  --max-wall-clock-seconds 86400 \
  --no-llm
```

The orchestrator reads `ready_for_full_run` from the gate file before spawning
workers. If `false`, it exits with code 2 and does not spawn.

### 7.3 Owner waiver (emergency only)

```bash
python scripts/run_vectorbt_paid_screen_v2.py \
  --units-jsonl runtime/reports/vbt_full_units.jsonl \
  --out "$OUT" \
  --vectorbt-scope paid-compute \
  --workers 230 \
  --owner-waiver "gate pending re-evaluation after events.csv refresh; pilot+smoke pass recorded separately" \
  --max-wall-clock-seconds 86400 \
  --no-llm
```

The waiver reason string is logged to stderr. Do not use a waiver for a full
rent unless the gate failure is understood and the reason is documented.

### 7.4 Stall-watch mode (during a full run)

```bash
python scripts/validate_paid_screen_ready_gate.py \
  --watch-manifest "$OUT/paid_screen_run_manifest.json" \
  --stall-minutes 30
```

Returns 0 if progress was made since the last poll, 1 if no progress (stall
suspected), 2 if the manifest is missing. Run on a 5-minute cron or in a
supervisor loop.

### 7.5 Re-validate the gate after an environment refresh

If `events.csv`, the lake manifest, or the repo `HEAD` changes, the gate must
be re-evaluated because the hashes will differ:

```bash
python scripts/validate_paid_screen_ready_gate.py \
  --pilot-artifact "$VBT_PILOT_ARTIFACT" \
  --smoke-manifest "$VBT_SMOKE_MANIFEST" \
  --out runtime/reports/paid_screen_ready_gate.json \
  --repo-root "$HFT3_REPO"
```

A stale gate file with `ready_for_full_run: true` from a prior environment is
**not** valid after a hash change; re-run the pilot and smoke if the hashes
drift (see `VBT_PAID_SCREEN_RUNBOOK.md` error→action matrix: "Gate hash
mismatch → re-sync repo/events.csv/manifest; re-pilot").

---

## Quick reference — flag map

| Flag | v1 | v2 | Notes |
|------|----|----|------|
| `--units-jsonl` | ✓ | ✓ | Same JSONL format; v2 reads structured fields. |
| `--out` | ✓ | ✓ | Run directory. |
| `--vectorbt-scope` | ✓ | ✓ | `paid-compute` for rent. |
| `--workers` | ✓ | ✓ | v2: long-lived workers; v1: subprocess pool. |
| `--max-wall-clock-seconds` | ✓ | ✓ | Per-run wall-clock budget. |
| `--ready-gate-file` | ✓ | ✓ | Required when `--workers > 1` (unless `--owner-waiver`). |
| `--owner-waiver` | ✓ | ✓ | Reason string; logged. |
| `--dry-run` | ✓ | ✓ | v2 prints unit/batch/worker counts. |
| `--no-llm` | ✓ | ✓ | Default true on v2. |
| `--repo-root` | ✓ | ✓ | Defaults to script parent. |
| `--resume` | — | ✓ | v2-only: skip units with valid artifacts. |
| `--max-batches-before-recycle` | — | ✓ | v2-only; default 100. |
| `--cache-memory-limit-mb` | — | ✓ | v2-only; default 4096. |
| `--cache-max-entries` | — | ✓ | v2-only; default 1000. |
| `--events-csv-hash` | — | ✓ | v2-only; auto-derived if omitted. |
| `--lake-manifest-hash` | — | ✓ | v2-only; auto-derived if omitted. |
| `--batch-timeout-seconds` | — | ✓ | v2-only; default 1800. |

---

## Quick reference — scenario → command

| Scenario | Section |
|----------|---------|
| Smoke run (v2) | §1 |
| Parity run (old vs new) | §2 |
| Benchmark run | §3 |
| Resumable production run | §4 |
| Artifact validation | §5 |
| Dry run | §6 |
| Ready gate enforcement | §7 |
| Rollback to v1 | `PAID_SCREEN_MIGRATION_PLAN.md` §3 |
