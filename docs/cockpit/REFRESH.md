# Cockpit + workbench refresh (canonical repo)

**Canonical path:** `C:\Users\MSI\repos\hft3` on `main` — see [docs/REPO_STATE.md](../REPO_STATE.md).

## Cockpit (port 8080)

1. Close any existing cockpit window (Ctrl+C in the launch terminal) or confirm port 8080 is free.
2. From repo root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/cockpit_launch.ps1 -Rebuild
```

3. Hard-refresh the browser (Ctrl+F5) at `http://127.0.0.1:8080/`.

**What you should see after refresh:**

| View | New / updated truth |
|------|---------------------|
| **Top bar** | `main@<short-sha>` git badge from live repo |
| **System → Repo context** | Canonical path, branch, commit, REPO_STATE HEAD summary |
| **System → Validation honesty** | Pointer to `docs/VALIDATION_HONESTY.md` + M6 monitor doc |
| **Pipeline → M6 sweep** | Read-only Vast/local tracking (no launch button); monitor doc path |
| **Pipeline → Latency evidence** | `component_bands` table, live placement summary, CC ingest / regime flags |
| **Models** | Slug registry count from `model_registry.yaml`, including hypothesis, structural, and RL entries |
| **Control** | Retired jobs removed (`cme_m6_universe_sweep` no longer listed) |

**Vast M6 mirror (optional):** sync remote log/checkpoint per `runtime/monitor/universe_M6_full_watch.md` so Pipeline sweep state reflects Vast, not a stale local 14-worker run.

## Workbench Streamlit

```powershell
cd C:\Users\MSI\repos\hft3
$env:PYTHONPATH = "C:/Users/MSI/.claude/shims;$PWD;$PWD/packages"
streamlit run apps/workbench/ui/app.py
```

**Updated tabs:**

- **System** — repo context (branch, commit, REPO_STATE summary)
- **Execution & Latency** — HftBacktest `component_bands` + live placement from `latency_truth.json`
- **Registry & Data** — slug registry count from `model_registry.yaml`

Verify charter: `powershell -File scripts/verify_workbench.ps1` (when changing workbench code).
