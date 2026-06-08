# Autonomous Workbench audit

Audit date: 2026-06-08  
Branch: `chore/repo-cleanup-and-data-fill`  
Git SHA: `cfa564fe5740b94f07a3360fede0d725806b2574`  
Repo root: `C:\Users\MSI\Documents\New project`

Every claim below is cited with `file:line` or `file:function`. No inference beyond what is present in the cited code.

---

## 1. Current canonical Workbench launch command

**Verified by `apps/workbench/__main__.py:227` + `scripts/launch_workbench.ps1:140`.**

```
python -m streamlit run apps/workbench/ui/app.py --server.headless true --server.port 8501
```

The desktop `.lnk` calls `scripts/launch_workbench.ps1`, which runs the preflight then issues the above. There is no `python -m workbench` invocation for the UI; the `workbench` Python entrypoint (`apps/workbench/__main__.py`) exposes only `run | campaign | list | verify-data | imbalance-ablation` subcommands and is not what serves the UI.

## 2. Windows shortcut target

`C:\Users\MSI\Desktop\HFT3 Workbench.lnk` (created by `scripts/create_workbench_desktop_shortcut.ps1:20-37`).

```
TargetPath     : powershell.exe
Arguments      : -NoProfile -ExecutionPolicy Bypass -WindowStyle Normal -File "C:\Users\MSI\Documents\New project\scripts\launch_workbench.ps1"
WorkingDirectory: C:\Users\MSI\Documents\New project
IconLocation   : C:\Users\MSI\AppData\Local\Programs\Python\Python312\python.exe,0
WindowStyle    : 1 (Normal — opens a console window)
```

The shortcut launches a PowerShell console that runs the launcher. The launcher does **not** write to any log file today (`scripts/launch_workbench.ps1:1-144` — no `Start-Transcript`, no `Out-File`). Failures print to the console only.

## 3. Repo root, git branch, git SHA, Python, venv, import path

| Item | Value | Source |
|------|-------|--------|
| Repo root | `C:\Users\MSI\Documents\New project` | `hft3_bootstrap.repo_root` → `_ROOT = Path(__file__).resolve().parent` (hft3_bootstrap.py:14) |
| Git branch | `chore/repo-cleanup-and-data-fill` | `git rev-parse --abbrev-ref HEAD` |
| Git SHA | `cfa564fe5740b94f07a3360fede0d725806b2574` | `git rev-parse HEAD` |
| Python | `C:\Users\MSI\AppData\Local\Programs\Python\Python312\python.exe` | `sys.executable` |
| `sys.prefix` | `C:\Users\MSI\AppData\Local\Programs\Python\Python312` | no venv active — system Python |
| `venv/` or `.venv/` | does not exist | `Test-Path` returns `False` |
| Import path | set by `hft3_bootstrap.setup_repo_paths()` (hft3_bootstrap.py:21-26) | adds `packages/`, `apps/`, `src/`, repo root to `sys.path` in that priority order |

The Workbench CLI uses the same bootstrap: `apps/workbench/__main__.py:10-12` calls `setup_repo_paths()` and stores the result as `_REPO`.

## 4. Current UI control path

```
[app.py]
  apps/workbench/ui/app.py:32-43 imports panels, including:
    - workbench.src.run.job_manager.{get_job_status, set_control, start_campaign_subprocess}
    - workbench.ui.flow_state.{campaign_progress_panel, navigate_to_tab, resolve_period_event}
    - workbench.ui.campaign_panel.{model_selector_panel, ...}

[user clicks "Run campaign"]
  apps/workbench/ui/campaign_panel.py:418-419
    st.button("Run campaign", key="wb__start_campaign") ->
    start_campaign_for_selection(...)  (apps/workbench/ui/flow_state.py:136-176)

[start_campaign_for_selection]
  flow_state.py:144-152  — stop the prior subprocess if any (writes control.json = "stop")
  flow_state.py:153-160  — calls job_manager.start_campaign_subprocess(...)
  flow_state.py:174      — writes control.json = "run" via set_control
  flow_state.py:175      — navigate_to_tab("Backtest Results")

[start_campaign_subprocess]
  apps/workbench/src/run/job_manager.py:26-89
    - resolves model_id via features_engine.src.model_registry.resolve_model_id
    - makes a campaign_id via campaign_runner.make_campaign_id (job_manager.py:23,41)
    - writes composition.json if defensive stubs are set (job_manager.py:43-46)
    - spawns Popen:  python -m workbench campaign --model ... --symbol ... --campaign-id ...
      (job_manager.py:48-89)
    - env: PYTHONPATH from pythonpath_entries(repo_root); HFT3_CPP_STACK_VERIFY=off

[python -m workbench campaign dispatches to]
  apps/workbench/__main__.py:150-194  ->  run_campaign()

[run_campaign]
  apps/workbench/src/run/campaign_runner.py:305-736
    1. resolves model_id, loads binding, walk-forward config, periods (lines 321-339)
    2. writes control.json = "run" + status.json = "running" (lines 352-353)
    3. writes campaign.json metadata (line 374)
    4. (optional) WFC matrix via run_full_matrix_oos -> save_matrix_rows -> evaluate_wfc_gate
       -> write_wfc_artifacts (lines 415-487)
    5. for each period:
         - _wait_if_paused() (line 491, also 544 inside event loop)
         - lists events, identifies missing/runnable (lines 502-510)
         - for each runnable event:
             - engine.run(...)  (line 558)  -> WorkbenchEngine.run in engine.py
             - copies event artifacts into period_dir/events/<event_id>/ (lines 587-604)
             - copies trades.parquet if present (lines 611-613)
             - appends to event_outcomes (lines 615-626)
         - writes period_summary.json (line 650)
    6. run_robustness_pack (line 661)
    7. writes summary.json + diagnostics.json (lines 724-725)
    8. writes final status.json = state.lower() (line 726)

[WorkbenchEngine.run]
  apps/workbench/src/run/engine.py:51-?  produces a report dict (net_pnl, num_trades,
    survives_cpp_execution_delay, artifact_dir, run_id, ...) which run_campaign copies
    into the campaign tree.

[Artifacts land in]
  campaign_dir_for(repo, campaign_id)  ->  apps/workbench/src/artifacts/paths.py:50-52
    ->  workbench_runs_dir_for(repo)
      ->  prefers artifacts/research_cards/workbench_runs/ (if exists), else
          artifacts/workbench_runs/, else research_cards/workbench_runs/

  The "preferred" path is what `paths.py:31-39` checks at lookup time. There is no
  on-disk `artifacts/research_cards/workbench_runs/` currently, but the existing
  `artifacts/research_cards/workbench_runs/<campaign_id>/` shows that campaigns do
  get written there.

[UI shows progress via poll]
  apps/workbench/ui/flow_state.py:207-253
    @st.fragment(run_every=2s)
    campaign_progress_panel  reads status.json, period, event_id via get_job_status
    shows st.status / st.error / st.success / st.warning based on state.
```

## 5. Current supported commands in `control.json`

From `apps/workbench/src/run/campaign_runner.py:83-112`:

| `command` value | Source | Behaviour |
|-----------------|--------|-----------|
| `run` (default) | `_read_control` line 86-90 returns `"run"` if file missing/invalid | runner proceeds |
| `pause` | `_wait_if_paused` line 105-107 | runner writes `status.json = {"state": "paused"}` and `time.sleep(1.0)` until control changes |
| `stop` | `_wait_if_paused` line 108-109 (within pause loop) and line 110-111 (after loop) | returns `False`, callers mark status `CANCELLED` and break out of the period/event loops |

These are the only three commands the runner understands. There is no `resume` command — the runner treats any non-`pause` non-`stop` value as `run` (default).

The UI side only emits `run` (line 174), `pause` (campaign_panel.py:430), and `stop` (campaign_panel.py:433). It does not call `set_control(..., "resume")` anywhere.

## 6. Pause/resume — actually implemented or only mentioned?

| | |
|---|---|
| Pause | **Implemented and wired.** Runner: `campaign_runner.py:103-112` (`_wait_if_paused`). UI: `campaign_panel.py:430`. |
| Resume | **NOT a separate command.** The runner auto-resumes from any non-`pause` non-`stop` value (`campaign_runner.py:83-90` defaults to `"run"`). So writing `command="run"` after a pause does in fact resume. The UI never labels the action as "Resume" — clicking "Pause" toggles, but there is no dedicated Resume button. |
| Cooperative | **Yes** for pause. **No** for stop: `_wait_if_paused` returns `False` on `stop`, but the period/event loops only check this at safe checkpoints (start of period, start of each event). A long-running `engine.run(...)` call will not be interrupted mid-flight. |
| Skips already-completed jobs | **Not implemented.** On restart, `run_campaign` rebuilds the period/event list and re-executes from scratch. The runner does not read prior `period_summary.json` to short-circuit completed events. |

## 7. Does the Workbench read the active campaign evidence, or latest by timestamp?

The UI binds state to the active campaign via `st.session_state.wb_active_campaign` (flow_state.py:145, 162). The poll reads `get_job_status(repo, campaign_id)` → reads `campaign_dir_for(repo, campaign_id) / "status.json"` (job_manager.py:99-110). The campaign-progress panel updates state from this.

However, `pick_latest_event_with_aar` (flow_state.py:77-101) and `pick_first_event_with_aar` (flow_state.py:104-117) walk `campaign_dir/<active>/periods/` and pick by **artifact mtime or directory order**. There is no time-based stale-evidence risk **as long as the active campaign_id is correct**, but the UI has no hard guard against two different campaigns being open in adjacent sessions.

The bigger gap: the UI never re-reads `summary.json` after a campaign ends unless the user navigates. Once a campaign is "complete", the displayed numbers come from `on_campaign_finished` (flow_state.py:120-133) which selects the first event with AAR markers and then caches in session state. There is no "Refresh Evidence" button that re-reads the on-disk `summary.json` and recomputes aggregates.

## 8. Where the all-model / all-lane catalog is discovered

There is **no** `apps/workbench/src/run/all_lanes.py` (file does not exist). The `all_lanes/` directory under `runtime/workbench/` contains prior artifacts from a deleted orchestrator (the manifests reference timestamps from 2026-06-04 and 2026-06-05). No current Python code references `all_lanes` (grep returned 0 matches).

The current model discovery path is:
- `apps/workbench/src/registry/unified_registry.py` — `list_models()` and `build_models_config()` (called by `model_catalog.py:14`)
- `apps/workbench/src/registry/model_catalog.py:104-198` — `load_catalog()` returns 55 `CatalogEntry` objects
- `apps/workbench/__main__.py:103-108` — `workbench list` subcommand prints slugs

The campaign runner is **single-model**: `run_campaign(repo, model_id, symbol, ...)` (campaign_runner.py:305-320). The CLI subcommand `campaign` takes `--model` and `--symbol` (apps/workbench/__main__.py:51-69). There is no `all` / `full` / `multi` mode.

## 9. Where skipped / blocked / failed jobs are recorded

Inside `run_campaign`:
- **Data missing** → `PeriodResult.error = "DATA_MISSING: no NPZ for period events"` (campaign_runner.py:528), `period_summary.json` written (line 531), `status = "BLOCKED"` (line 532).
- **Sequential gate FAIL** → `status = "FAIL"` and break (line 652-654).
- **Cancelled** (stop) → `status = "CANCELLED"` and break (line 491-493, 545-546).
- **History gate fail** (no audit-grade) → `status = "DATA_INSUFFICIENT"`, no `period_summary.json` written (lines 376-384).
- **WFC FAIL/ERROR** → `status = "FAIL"`, `skip_periods = True` (lines 458-460), `wfc_status_for_events = "ERROR"`.

These are surfaced in `summary.json["periods"]` (campaign_runner.py:707) and `summary.json["status"]` (line 698). They are not surfaced as a per-job table; the campaign is a single (model, symbol) so there is no "skipped job" granularity beyond per-period / per-event.

The campaign_runner **does not** distinguish between resource-budget-blocked and data-missing; both surface as `status = "BLOCKED"`. There is no current "BLOCKED_RESOURCE_BUDGET" status string.

## 10. Trader metrics — where are they computed?

There is no centralized "trader metrics" computation in the current code. The metrics that exist:
- `PeriodResult` (campaign_runner.py:38-50) carries: `net_pnl, num_trades, expectancy, events_run, events_missing, survives_cpp`. No win-rate, profit factor, Sharpe, Sortino, drawdown, exposure, turnover, fees, latency percentiles.
- `robustness.pack.run_robustness_pack` (called at campaign_runner.py:661) produces `robustness.walk_forward` and `robustness.overfit_risk`, but is a return-curve statistic, not a trader PnL curve.
- The `engine.run` produces a `report` dict with `net_pnl`, `num_trades`, `survives_cpp_execution_delay`, `trades_vetoed_by_defense`. No equity curve, no trade ledger, no fee/slippage split.
- `trades.parquet` is copied into event dirs (campaign_runner.py:611-613) if the engine writes one. The engine imports `audit_records_to_dataframe` from `workbench.src.core.trade_audit` (engine.py:13), so a per-event audit may exist. **Not verified to contain a full trade ledger with timestamps, prices, fees.**

Honest verdict: today the system records `net_pnl, num_trades, expectancy` per period and per event. It does **not** compute or persist Sharpe, Sortino, drawdown, Calmar, profit factor, win rate, exposure time, turnover, average holding time, route distribution, or latency p50/p95/p99. Reporting any of these as "real" would be fabrication.

---

## 11. What the audit reveals about the gap to the spec

| Spec requirement | Today |
|------------------|-------|
| Start Full Autonomous Run (all models) | **Missing.** Campaign CLI is per-(model, symbol). Need to add an `autonomous` subcommand or wire a wrapper. |
| Pause | Implemented |
| Resume (dedicated button + command) | Implemented implicitly via "run" command but UI has no Resume button |
| Stop | Implemented |
| Reset Campaign | **Missing** in UI |
| Open Active Run Folder | **Missing** in UI |
| Refresh Evidence | **Missing** in UI |
| `planned_jobs.json` before execution | **Missing** — `run_campaign` writes `campaign.json` with metadata but no per-job plan |
| `evidence_snapshot.json` | **Missing** as a concept; only `summary.json` + `diagnostics.json` |
| `errors.jsonl` | **Missing** |
| `trade_ledger.csv/parquet` | Partial (`trades.parquet` if engine emits one) |
| `equity_curve.csv/parquet` | **Missing** |
| `coverage_report.json` | **Missing** as a campaign artifact; coverage info is in `summary.json["periods"][*].events_missing` |
| `pit_report.json` | **Missing** |
| `metrics.json` | **Missing** — periods embed `expectancy` but no full metrics file |
| `backend.log` | **Missing** per campaign |
| Trader metrics block | **Missing** |
| BLOCKED_RESOURCE_BUDGET status | **Missing** |
| All-models discovery | **Missing** — only single-model CLI today |
| Launcher log file | **Missing** — print to console only |
| Tests for start/pause/resume/stop | **Missing** as acceptance tests |
