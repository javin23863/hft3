# Paid-Screen Redesign — Migration & Rollback Plan

Status: **Ready for execution.** All 6 redesign phases are implemented and
verified by 214 passing tests across 8 test files
(`test_paid_screen_profiling`, `test_paid_screen_types`, `test_paid_screen_batch`,
`test_paid_screen_worker`, `test_paid_screen_cache`, `test_paid_screen_matrix`,
`test_paid_screen_hardening`, `test_paid_screen_parity_corpus`).

This document is the authoritative migration and rollback plan for switching
production paid-screen runs from the **v1 orchestrator**
(`scripts/run_vectorbt_paid_screen.py`) to the **v2 orchestrator**
(`scripts/run_vectorbt_paid_screen_v2.py`) and the redesigned execution path
(typed units → `group_units_by_batch_key` → `PaidScreenWorker` →
`screen_paid_batch` → `run_vectorbt_simulation_matrix`).

Related documents:
- `PAID_SCREEN_REDESIGN_DESIGN.md` — design, invariants, phase map.
- `PAID_SCREEN_BATCHING_KEY_SPEC.md` — 15-field `BatchingKey` contract.
- `PAID_SCREEN_CACHE_SPEC.md` — 5 content-addressed cache layers.
- `PAID_SCREEN_OPS_COMMANDS.md` — example commands for every operational
  scenario (smoke, parity, benchmark, resume, validation, dry run, gate).

---

## 1. Pre-migration checklist

The migration may only begin once **every** item below is green. Do not skip
items; each guards a distinct failure mode that is expensive to discover after
rent has started.

### 1.1 Test gate (mandatory)

```bash
cd "$HFT3_REPO"
python -m pytest tests/test_paid_screen_profiling.py \
                 tests/test_paid_screen_types.py \
                 tests/test_paid_screen_batch.py \
                 tests/test_paid_screen_worker.py \
                 tests/test_paid_screen_cache.py \
                 tests/test_paid_screen_matrix.py \
                 tests/test_paid_screen_hardening.py \
                 tests/test_paid_screen_parity_corpus.py \
                 -q --tb=short
```

**Required:** all 214 tests pass, 0 failures, 0 errors. Record the exact count
and the `git rev-parse HEAD` in the migration log:

```bash
git rev-parse HEAD > runtime/reports/paid_screen_migration_baseline_git.txt
python -m pytest <files above> -q --tb=no | tail -1 \
  > runtime/reports/paid_screen_migration_baseline_tests.txt
```

### 1.2 Loop↔matrix parity anchor (mandatory)

The parity corpus (`tests/fixtures/paid_screen_parity_corpus.py`) is the
deterministic regression anchor for the loop↔matrix invariant. Re-run it as a
standalone check so the baseline is explicit:

```bash
python -m pytest tests/test_paid_screen_matrix.py \
  -k "chunk_size or identical or parity" -q --tb=short
```

**Required:** every `chunk_size`-independence and loop-mode-identity assertion
passes. This is the proof that v2 matrix mode produces byte-for-byte identical
per-trial results versus loop mode (design invariant 5).

### 1.3 v1 baseline record (mandatory)

Run the **v1 orchestrator** on the parity corpus (or an existing smoke unit
set) and persist the baseline manifest + artifacts. This is the "before"
snapshot used by the parity run in step 2.

```bash
export VBT_MIGRATION_BASELINE_DIR="runtime/reports/paid_screen_migration_v1_baseline"

python scripts/generate_vbt_paid_units_jsonl.py \
  --out runtime/reports/vbt_migration_units.jsonl \
  --smoke-count 12 \
  --symbols MES.v.0,ES.v.0 \
  --event-types CPI,NFP \
  --model-id HYP_5

python scripts/run_vectorbt_paid_screen.py \
  --units-jsonl runtime/reports/vbt_migration_units.jsonl \
  --out "$VBT_MIGRATION_BASELINE_DIR" \
  --vectorbt-scope paid-compute \
  --workers 1 \
  --max-wall-clock-seconds 3600 \
  --no-llm
```

**Required:**
- `paid_screen_run_manifest.json` exists in the baseline dir.
- `status` is `complete` (no `failed_work_units`).
- Every `OK` unit has a `units/<unit_id>/screening_artifact.json` that passes
  `validate_screening_artifact`.
- Record `expected_work_units`, `completed_work_units`, `units_per_hour`, and
  the manifest path in `runtime/reports/paid_screen_migration_baseline.json`.

### 1.4 Environment & hashes

- `HFT3_NPZ_ROOT` and `HFT3_MANIFEST_PATH` point at the same lake used by the
  v1 baseline (hashes must match across the comparison).
- `events_csv_hash` and `lake_manifest_hash` are recorded from the v1 baseline
  manifest and will be passed explicitly to v2 via `--events-csv-hash` and
  `--lake-manifest-hash` so cache keys are directly comparable.
- `vectorbt[rust]` is installed and `import vectorbt` succeeds on the host that
  will run v2.
- Git working tree is clean at the recorded `HEAD`; no uncommitted changes to
  `packages/backtest_pipeline/src/paid_screen_*.py` or `scripts/run_vectorbt_paid_screen_v2.py`.

### 1.5 Rollback pre-arming

- Confirm `scripts/run_vectorbt_paid_screen.py` (v1) is unchanged and still
  runnable on the same unit JSONL (it is; v1 and v2 coexist as separate scripts).
- Tag the current `HEAD` so rollback has an exact restore point:
  ```bash
  git tag paid-screen-v2-migration-start
  ```
- Record the tag name in the migration log.

---

## 2. Migration steps

The migration is **gradual**: v2 is exercised first on a single worker, then
on a small parallel batch, then on the full rent topology. Each stage has an
explicit gate; do not advance until the gate passes.

### Step 1 — Add the `--execution-mode` selector

The two orchestrators coexist as separate scripts. Introduce a thin dispatch
shim so operators can select the execution mode with one flag without
memorizing two script paths. This is a convenience layer; it does not change
either orchestrator's internals.

Create `scripts/run_paid_screen.py` (dispatch shim):

```python
#!/usr/bin/env python3
"""Dispatch shim: select v1 or v2 paid-screen orchestrator via --execution-mode."""
import argparse, subprocess, sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--execution-mode", choices=["v1", "v2"], default="v2")
    known, rest = parser.parse_known_args()
    script = "run_vectorbt_paid_screen.py" if known.execution_mode == "v1" \
             else "run_vectorbt_paid_screen_v2.py"
    return subprocess.call([sys.executable, str(_REPO / "scripts" / script)] + rest)

if __name__ == "__main__":
    raise SystemExit(main())
```

**Why a shim and not an in-place flag on v1?** v1 spawns a `run_pipeline.py`
subprocess per unit and parses free-text thesis strings; v2 uses typed units and
long-lived workers. They share no execution code, so a dispatch shim keeps each
path intact and auditable — which is what makes rollback trivial (step 3).

### Step 2 — Parity run (v1 vs v2 on the same corpus)

Run v2 on the **same unit JSONL** used for the v1 baseline in §1.3, with the
**same hashes** so cache keys are comparable. Use `--workers 1` first so any
discrepancy is attributable to the execution path, not concurrency.

```bash
export VBT_MIGRATION_V2_PARITY_DIR="runtime/reports/paid_screen_migration_v2_parity"

python scripts/run_vectorbt_paid_screen_v2.py \
  --units-jsonl runtime/reports/vbt_migration_units.jsonl \
  --out "$VBT_MIGRATION_V2_PARITY_DIR" \
  --vectorbt-scope paid-compute \
  --workers 1 \
  --max-wall-clock-seconds 3600 \
  --no-llm \
  --resume
```

**Parity gate (all required):**

| Check | Method |
|-------|--------|
| Same unit set | `expected_work_units` equal between v1 baseline and v2 parity manifests |
| Same statuses | For each `unit_id`, v1 status == v2 status (OK/OK_CACHED/ERROR/SKIPPED) |
| Same promoted/rejected IDs | For each OK unit, `promoted_ids` and `rejected_ids` match between v1 and v2 artifacts |
| Artifact schema valid | `validate_screening_artifact` passes on every v2 `units/<unit_id>/screening_artifact.json` |
| No-lookahead proof present | `no_lookahead_signal_shift_proof` non-empty on every v2 artifact |
| Manifest honest | v2 `status == "complete"` and `completed + skipped + failed == expected` |

A helper diff is recommended (can be added to
`scripts/compare_paid_screen_manifests.py`):

```bash
python scripts/compare_paid_screen_manifests.py \
  --v1 "$VBT_MIGRATION_BASELINE_DIR/paid_screen_run_manifest.json" \
  --v2 "$VBT_MIGRATION_V2_PARITY_DIR/paid_screen_run_manifest.json"
```

**If parity fails:** do not advance. File the discrepancy in the migration log,
diff the per-unit artifacts, and re-run the relevant
`test_paid_screen_matrix.py` parity assertions locally. The corpus is the
regression anchor — a parity failure is a correctness bug, not a tuning issue.

### Step 3 — Gradually switch workers

Once parity passes at `--workers 1`, scale up in stages. Each stage must
produce a `complete` manifest with `failed_work_units == 0` before advancing.

| Stage | Workers | Units | Topology | Gate |
|-------|---------|-------|----------|------|
| A | 1 | parity corpus | workstation | parity gate (§2) |
| B | 4–8 | 8–16 smoke units | workstation | smoke pass checklist (see `PAID_SCREEN_OPS_COMMANDS.md` §Smoke run) |
| C | 16 | 50–100 units | workstation or small rent | `complete`, 0 failures, throughput recorded |
| D | ≥230 | full Stage A scope | Vast 256 vCPU | `--ready-gate-file` enforced; full-run completion checklist |

At each stage record `units_per_hour` so the throughput trend is visible. v2
should be strictly faster than v1 at the same worker count because it
amortizes imports and VectorBT/Rust init across batches (design §1).

### Step 4 — Cut over the default

Once stage D completes with a `complete` manifest and validated artifacts:

1. Flip the dispatch shim default from `v2` (already the default in the shim
   above) — confirm the default by running `--help` on the shim.
2. Update the runbook (`VBT_PAID_SCREEN_RUNBOOK.md`) Phase B and Phase D command
   blocks to use the shim with `--execution-mode v2` (or the v2 script
   directly) so new operators land on v2 by default.
3. Tag the cutover commit:
   ```bash
   git tag paid-screen-v2-cutover
   ```
4. Leave v1 in place. It is the rollback target (see §3).

---

## 3. Rollback plan

Rollback is **always available** because v1 and v2 are separate scripts that
share no execution code. There is no in-place mutation to undo.

### 3.1 When to roll back

- v2 parity run fails and the root cause is not understood within the
  maintenance window.
- v2 smoke or full run produces `failed_work_units > 0` with errors that v1 does
  not produce on the same units.
- v2 manifest status is not `complete` and cannot be made `complete` by
  `--resume`.
- A correctness regression is suspected (promoted/rejected IDs diverge from v1
  on validated artifacts).

### 3.2 Rollback procedure

1. **Stop v2 workers.** Kill the v2 process pool; preserve the v2 run directory
   for post-mortem (do not delete it — the failure diagnostics in
   `failure_diagnostics.json` are the evidence).
2. **Restore the v1 path.** No code change is needed; v1 is still
   `scripts/run_vectorbt_paid_screen.py`. To pin to the exact pre-migration
   state:
   ```bash
   git checkout paid-screen-v2-migration-start -- scripts/run_vectorbt_paid_screen.py
   ```
   (v1 was not modified during migration, so this is a no-op in the common
   case; the checkout only guards against accidental edits.)
3. **Re-run on v1** with the same unit JSONL and hashes:
   ```bash
   python scripts/run_vectorbt_paid_screen.py \
     --units-jsonl runtime/reports/vbt_full_units.jsonl \
     --out "research_cards/pipeline_runs/${VBT_FULL_RUN_ID}_rollback_v1" \
     --vectorbt-scope paid-compute \
     --workers 230 \
     --ready-gate-file runtime/reports/paid_screen_ready_gate.json \
     --max-wall-clock-seconds 86400 \
     --no-llm
   ```
4. **Confirm v1 manifest is `complete`** and artifacts validate before
   declaring the rollback successful.
5. **File a rollback record** in
   `runtime/reports/paid_screen_rollback_<date>.json` with: v2 run dir, v2
   failure summary, v1 rollback run dir, v1 manifest status, root-cause
   hypothesis.

### 3.3 What is preserved on rollback

- **v1 path is intact.** `run_vectorbt_paid_screen.py` and its
  `run_pipeline.py` subprocess-per-unit model are unchanged. No v2 code is
  imported by v1.
- **Unit JSONL is shared.** v1 and v2 consume the same JSONL format (v2 reads
  the additional structured fields via `PaidScreenUnit.from_jsonl_row`, and
  ignores fields it does not use; v1 reads only the v1 fields). The same
  `vbt_full_units.jsonl` works for both.
- **Ready gate is shared.** `paid_screen_ready_gate.json` is produced by
  `validate_paid_screen_ready_gate.py` and is consumed by both orchestrators
  unchanged.
- **Artifacts are independent.** v1 and v2 write to separate run directories;
  rolling back does not overwrite v2 artifacts, so the post-mortem evidence is
  preserved.

### 3.4 Partial rollback (resume-only)

If v2 failed mid-run with some units already validated, you may resume v2
rather than full-rollback to v1, **only if** the failure is recoverable (e.g.
wall-clock budget exhaustion, a transient worker crash):

```bash
python scripts/run_vectorbt_paid_screen_v2.py \
  --units-jsonl runtime/reports/vbt_full_units.jsonl \
  --out "$VBT_FULL_RUN_ID" \
  --vectorbt-scope paid-compute \
  --workers 230 \
  --ready-gate-file runtime/reports/paid_screen_ready_gate.json \
  --max-wall-clock-seconds 86400 \
  --no-llm \
  --resume
```

`--resume` skips units whose `units/<unit_id>/screening_artifact.json` already
validates (Phase 6 hardening: valid-skip / invalid-recompute). If the failure is
a correctness regression, do **not** resume — roll back fully, because
already-validated artifacts may carry the regression.

---

## 4. Risk assessment

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | Loop↔matrix parity breaks on real data (not covered by the synthetic corpus) | Low | High (silent wrong results) | Parity gate in §2 runs on the real smoke corpus before any full rent. The corpus fixture covers sparse/dense/missing-data/budget dimensions; any new failure mode is a correctness bug to fix before proceeding. |
| R2 | `BatchingKey` incompatibility rejects units that v1 batched together | Low | Medium (fewer batches, not wrong results) | This is the intended content-addressed behavior. Verify the batch count in the v2 dry run (`--dry-run`) matches expectations; a lower batch count than v1 is expected and correct. |
| R3 | Worker crash isolates a batch but the manifest is not flushed | Low | Medium (partial manifest) | Phase 6 hardening (`test_paid_screen_hardening.py`) verifies worker-crash isolation and manifest honesty (`determine_manifest_status` never returns `complete` when `failed > 0`). `--resume` recovers. |
| R4 | Cache memory pressure on long runs | Medium | Low (eviction, not corruption) | `BoundedLRUCache` evicts LRU; `_recycle()` clears the cache after `max_batches_before_recycle` (default 100) without restarting the process. Tune `--cache-memory-limit-mb` and `--max-batches-before-recycle` for the host. |
| R5 | v2 slower than v1 at low worker count due to spawn overhead | Medium | Low | v2 uses `spawn` context (one-time cost). At `--workers 1` the overhead is negligible; at scale the amortized init dominates favorably. Benchmark (see `PAID_SCREEN_OPS_COMMANDS.md` §Benchmark run) to confirm. |
| R6 | Operator accidentally runs v1 on a v2-tagged run directory | Low | Low (separate dirs) | Run directories are timestamped and orchestrator-versioned (`orchestrator_version: "v2"` in the v2 manifest). The dispatch shim defaults to v2; v1 is opt-in via `--execution-mode v1`. |
| R7 | `events_csv_hash` / `lake_manifest_hash` drift between v1 baseline and v2 run | Medium | High (cache keys not comparable, parity invalid) | Pass `--events-csv-hash` and `--lake-manifest-hash` explicitly to v2 with the values from the v1 baseline manifest (§1.4). The ready gate also enforces hash match. |
| R8 | Rust runtime proof fails under v2 worker init | Low | High (fail-closed rejections) | v2 fails closed (design invariant 6): missing VectorBT/Rust proof produces explicit `RejectedCandidate` rows with `rust_runtime_proof_missing_fail_closed`. Detect in the parity run; fix the Vast host setup before full rent. |
| R9 | Artifact schema drift between v1 and v2 | Low | High (validation fails) | Both orchestrators write artifacts validated by the same `validate_screening_artifact`. The parity gate (§2) enforces schema validity on v2 artifacts before advancing. |
| R10 | Unbounded retry on `--resume` hides a persistent failure | Medium | Medium | `--resume` only skips units with **valid** artifacts; invalid artifacts are recomputed. A unit that fails every time will re-run each resume — this is correct. Monitor `failed_work_units` across resumes; if it does not decrease, stop and investigate. |

---

## 5. Verification criteria

Migration is **complete** only when all of the following hold. Each is
verifiable by a concrete command (see `PAID_SCREEN_OPS_COMMANDS.md` for the
full command forms).

### 5.1 Correctness

- [ ] v2 parity run (§2) passes the parity gate: same unit set, same statuses,
      same promoted/rejected IDs, valid artifact schema, no-lookahead proof
      present on every artifact.
- [ ] `test_paid_screen_matrix.py` chunk-independence and loop-mode-identity
      assertions pass (re-run as part of the test gate in §1.1).
- [ ] No `RejectedCandidate` row has a `*_fail_closed` stop reason on units that
      v1 screened successfully (this would indicate a Rust/VectorBT
      environment regression, not a data issue).

### 5.2 Manifest honesty

- [ ] v2 `paid_screen_run_manifest.json` has `status == "complete"`.
- [ ] `completed_work_units + skipped_work_units + failed_work_units ==
      expected_work_units` (exact equality).
- [ ] `failed_work_units == 0` (or, if non-zero, each failure has a named
      stop reason in `failure_diagnostics.json` and is owner-accepted).
- [ ] `orchestrator_version == "v2"` in the manifest.

### 5.3 Artifact integrity

- [ ] Every `OK`/`OK_CACHED` unit has `units/<unit_id>/screening_artifact.json`
      passing `validate_screening_artifact`.
- [ ] Every artifact has a non-empty `no_lookahead_signal_shift_proof`.
- [ ] `events_csv_hash` and `lake_manifest_hash` in every artifact match the
      values passed to the orchestrator (no drift).

### 5.4 Performance

- [ ] v2 `units_per_hour` ≥ v1 `units_per_hour` at the same worker count
      (recorded in the baseline and parity manifests).
- [ ] v2 cache hit rate is non-zero on the parity corpus (the NPZ/bar/feature
      layers should hit across units sharing an `event_id`); record
      `hit_count`/`miss_count` from the worker profiler summaries in the
      manifest.

### 5.5 Operability

- [ ] `--resume` recovers an interrupted run without recomputing valid
      artifacts (verify by interrupting a smoke run and resuming).
- [ ] `--dry-run` prints the unit count, batch count, and worker count without
      executing (verify with the command in `PAID_SCREEN_OPS_COMMANDS.md`
      §Dry run).
- [ ] Ready gate (`validate_paid_screen_ready_gate.py`) exits 0 on the v2
      smoke manifest (verify with the command in
      `PAID_SCREEN_OPS_COMMANDS.md` §Ready gate enforcement).
- [ ] Rollback drill: run v1 on the same unit JSONL and confirm a `complete`
      manifest (perform once, before the cutover, as the final pre-migration
      confidence check).

### 5.6 Sign-off record

Write the final verification record to
`runtime/reports/paid_screen_migration_complete.json`:

```json
{
  "migration_date_utc": "<ISO>",
  "git_head": "<sha>",
  "cutover_tag": "paid-screen-v2-cutover",
  "baseline_tag": "paid-screen-v2-migration-start",
  "v1_baseline_manifest": "<path>",
  "v2_parity_manifest": "<path>",
  "v2_full_manifest": "<path>",
  "parity_gate": "pass",
  "test_count": 214,
  "v2_units_per_hour": <number>,
  "v1_units_per_hour": <number>,
  "rollback_drill_v1_manifest": "<path>",
  "signoff": "owner"
}
```

Do not declare the migration complete until this record is written and every
checkbox above is true.