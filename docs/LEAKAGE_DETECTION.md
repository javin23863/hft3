# Workbench Leakage Detection

The Workbench has a central active-run leakage detector. It is meant to run
before model execution so stale artifacts, cross-run evidence, and unsafe
feature rows cannot silently enter an all-lane run.

Run it with:

```powershell
$env:PYTHONPATH='apps;packages'
python -m apps.workbench leakage-detect
```

The detector writes:

- `runtime/workbench/all_lanes/<run_id>/leakage_detection.json`
- `runtime/workbench/all_lanes/<run_id>/leakage_detection.md`

## Checks

- Active run manifest exists and matches the selected run.
- Generated artifact roots do not contain untracked previous-run evidence.
- Tracked generated artifacts are listed in `rejected_stale_artifacts.json`.
- Active-run artifacts do not declare a different `run_id`.
- Cross-lane feature fabric passes the Workbench evidence gate.
- Cross-lane feature rows pass point-in-time validation.
- Legacy `workbench_campaign`, `autonomous`, and `crypto_lane` sources stay blocked while an all-lane run is active.

## Status Meaning

`PASS` means the current active run boundary is clean enough for model execution
to start.

`FAIL` means the pipeline should not start. The JSON report lists exact blocking
gates and paths so the issue can be fixed instead of hidden.

Run a fresh boundary when generated artifacts are stale:

```powershell
$env:PYTHONPATH='apps;packages'
python -m apps.workbench fresh-start --confirm-hard-delete
python -m apps.workbench all-lanes --run-id <fresh_run_id>
python -m apps.workbench leakage-detect --run-id <fresh_run_id>
```
