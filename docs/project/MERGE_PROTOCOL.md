# HFT3 Merge Protocol

This protocol prevents parallel phase work from conflicting or accidentally changing execution behavior.

## Required Gates Per Phase Branch

1. GraphGate before edits and GraphPost after edits.
2. Contract reviewed against `PHASE_CONTRACTS.md`.
3. Targeted phase tests pass.
4. Relevant prior Trade Manager phase tests pass.
5. Read-only review reports no red findings.
6. No external broker/Rithmic routing changes.
7. `git status` shows only intended files staged.

## Branch Rules

| Branch Type | Allowed Changes | Disallowed Changes |
|---|---|---|
| Phase branch | owned module, owned tests, owned docs | shared `manager.py`, adapters, Rithmic, unrelated docs |
| Validation branch | docs, tests, test scripts, test-support helpers | product behavior |
| Integration branch | shared Trade Manager wiring, cross-phase tests | live execution routing |
| CHI404 execution branch | explicitly approved external broker work | dev-workstation routing |

## Integration Flow

1. Merge or cherry-pick one phase branch into `integration/trade-manager-20-23`.
2. Run that phase's targeted tests.
3. Run all Trade Manager phase tests from 14 through latest integrated phase.
4. Run production-safety tests when risk, monitor, kill switch, or execution boundary is touched.
5. Update docs and validation matrix.
6. Run GraphPost.
7. Repeat for the next phase branch.

## Required Commands

Phase branch minimum:

```powershell
$env:PYTHONPATH = "packages;apps"
python -m pytest tests/test_trade_manager_phaseXX.py -q
git diff --check
graphify update . --force
```

Integration branch minimum after Phase 20 lands:

```powershell
$env:PYTHONPATH = "packages;apps"
python -m pytest tests/test_trade_manager_phase14.py tests/test_trade_manager_phase15.py tests/test_trade_manager_phase16.py tests/test_trade_manager_phase17.py tests/test_trade_manager_phase18.py tests/test_trade_manager_phase19.py tests/test_trade_manager_phase20.py -q
python -m pytest tests/test_production_safety.py -q
python -m economic_event_universe.cli validate
git diff --check
graphify update . --force
```

Integration branch minimum after all Phase 20-23 modules land:

```powershell
$env:PYTHONPATH = "packages;apps"
python -m pytest tests/test_trade_manager_phase14.py tests/test_trade_manager_phase15.py tests/test_trade_manager_phase16.py tests/test_trade_manager_phase17.py tests/test_trade_manager_phase18.py tests/test_trade_manager_phase19.py tests/test_trade_manager_phase20.py tests/test_trade_manager_phase21.py tests/test_observer_view_read_only.py tests/test_trade_manager_phase23.py -q
python -m pytest tests/test_production_safety.py -q
python -m economic_event_universe.cli validate
git diff --check
graphify update . --force
```

If a phase branch is merged, its phase test file must exist and be included in the integration command. Missing expected phase tests are a merge blocker, not an optional adjustment.

## Commit Rules

1. Commit only intended files.
2. Do not stage unrelated untracked files such as local scratch or generated intake artifacts.
3. Do not amend unless explicitly requested.
4. Commit message format: `phase XX: add <capability>` for phase work, or concise descriptive message for project/validation work.
5. If graph tracked files changed during GraphPost, include them with the phase commit unless the team explicitly decides otherwise.

## Merge-Ready Definition

| Gate | Required State |
|---|---|
| Reviewer | no red findings; caveat if dedicated reviewer unavailable |
| Tests | targeted and scoped tests pass with command output |
| Graph | GraphPost complete; graph JSON valid if present |
| Safety | no unapproved execution/routing path |
| Docs | phase status, contracts, and validation matrix updated |
| Worktree | no unintended staged files |

If any gate is missing, status is `merge-ready: no`.
