# HFT3 Validation Matrix

This matrix defines what each workstream must run before handoff or merge. It complements `docs/VALIDATION_HONESTY.md` and must not be used to hide skipped tests.

## Core Status Commands

```powershell
git status --short
git diff --check
```

## Phase Branch Gates

| Workstream | Required Tests | Notes |
|---|---|---|
| Phase 20 Position Monitor | `python -m pytest tests/test_trade_manager_phase20.py -q` | Must prove no routing or flattening |
| Phase 21 Kill Switch | `python -m pytest tests/test_trade_manager_phase21.py -q` | Must prove decisions only, no adapter calls |
| Phase 22 Observer CLI | `$env:PYTHONPATH = "packages;apps"; python -m pytest tests/test_observer_view_read_only.py -q` | Must prove read-only behavior |
| Phase 23 Session Reporting | `$env:PYTHONPATH = "packages;apps"; python -m pytest tests/test_trade_manager_phase23.py -q` | Must validate 16 artifacts |
| Phase 24 Resumability Safety | `$env:PYTHONPATH = "packages;apps"; python -m pytest tests/test_autonomous_runner.py tests/test_autonomous_runner_recovery.py -q` | Must reject corrupted/partial runner artifacts and prove idempotent resume |
| Phase 25 Required Tests | `$env:PYTHONPATH = "packages;apps"; python -m pytest tests/test_phase25_required_tests.py -q` | Proves scoreboard consistency and blocker honesty |

## Integration Gates

Trade Manager integrated scope after Phase 20:

```powershell
$env:PYTHONPATH = "packages;apps"
python -m pytest tests/test_trade_manager_phase14.py tests/test_trade_manager_phase15.py tests/test_trade_manager_phase16.py tests/test_trade_manager_phase17.py tests/test_trade_manager_phase18.py tests/test_trade_manager_phase19.py tests/test_trade_manager_phase20.py -q
```

Trade Manager integrated scope after all Phase 20-23 modules land:

```powershell
$env:PYTHONPATH = "packages;apps"
python -m pytest tests/test_trade_manager_phase14.py tests/test_trade_manager_phase15.py tests/test_trade_manager_phase16.py tests/test_trade_manager_phase17.py tests/test_trade_manager_phase18.py tests/test_trade_manager_phase19.py tests/test_trade_manager_phase20.py tests/test_trade_manager_phase21.py tests/test_observer_view_read_only.py tests/test_trade_manager_phase23.py -q
```

After each phase merge, its test file must exist and be included. Missing expected phase tests block integration.

Safety scope:

```powershell
$env:PYTHONPATH = "packages;apps"
python -m pytest tests/test_production_safety.py -q
```

Event-universe gate:

```powershell
$env:PYTHONPATH = "packages;apps"
python -m economic_event_universe.cli validate
```

## Current Documented Scoreboard

The current documented scoreboard is `341/341 passing` across 26 test files in `docs/hft3_autonomous_pipeline_runbook.md` and `docs/hft3_traceability.md`. Phase 25 required-test closure is concrete and complete.

## Slow And External Tests

| Test Type | Default | Opt-In |
|---|---|---|
| External GPT-5.5 endpoint | skipped from normal scope with `-m "not slow"` | `HFT3_LIVE_LLM_TESTS=1` plus API key |
| CHI404 remote gates | not local by default | explicit CHI404 validation run |
| C++ golden binaries | skip if binaries absent | build required target first |

## Graph Gates

Before edits:

```powershell
.\scripts\graphify_gate.ps1 -Query "task-specific query"
.\scripts\graphify_pre_edit.ps1
```

After edits:

```powershell
graphify update . --force
$env:PYTHONPATH = "packages;apps"; python -c "import json; json.load(open('graphify-out/graph.json', encoding='utf-8')); print('graphify-out/graph.json: OK')"
```

If `graphify query` fails because the local ignored graph exceeds the size cap, rebuild with clustering enabled:

```powershell
graphify update . --force
```

Then rerun GraphGate.
