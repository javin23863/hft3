# HFT3 Workstreams

This file defines ownership boundaries so multiple phase teams can work at the same time without collisions.

## Global Rules

1. Do not edit another workstream's owned files without announcing it in the integration branch.
2. Do not edit `packages/trade_manager/manager.py` from a phase branch unless the phase contract explicitly requires it.
3. Do not edit `rithmic_gateway/`, `risk_engine/`, `packages/*/cpp/`, or CHI404 execution paths from these workstreams.
4. New modules must be inert until the integration branch wires them.
5. Tests must prove no adapter creation or order routing unless a future CHI404-only execution project explicitly authorizes it.

## Ownership Matrix

| Workstream | Owns | May Read | Must Not Touch |
|---|---|---|---|
| Phase 20 Position Monitor | `packages/trade_manager/monitor.py`, `tests/test_trade_manager_phase20.py` | `order_state.py`, `risk_layer.py`, `execution/interfaces.py` | `manager.py` integration, execution adapters |
| Phase 21 Kill Switch | `packages/trade_manager/kill_switch.py`, `configs/risk/kill_switch.yaml`, `tests/test_trade_manager_phase21.py` | Phase 20 contracts, production safety monitors | execution routing, C++ risk engine |
| Phase 22 Observer CLI | `apps/observer/`, `tests/test_observer_view_read_only.py` | session artifacts, position snapshots, kill-switch events | Trade Manager mutation APIs, adapters |
| Phase 23 Session Reporting | `packages/trade_manager/session.py`, `tests/test_trade_manager_phase23.py` | order intents, risk decisions, order states | observer CLI, execution adapters |
| Phase 24 Resumability Safety | `packages/hft3/research/run_autonomous.py`, `tests/test_autonomous_runner.py`, recovery tests/docs | checkpoints, registry atomicity, session artifacts | live adapters, Rithmic gateway, Trade Manager routing |
| Phase 25 Required Tests | `docs/project/VALIDATION_MATRIX.md`, validation scripts, missing required tests | all tests/docs | product behavior; test helpers only under `tests/` or test-support paths |
| Integration | `packages/trade_manager/manager.py`, cross-phase tests | all phase modules | CHI404/external routing unless explicitly approved |

## File Locks

| File or Directory | Lock Owner | Reason |
|---|---|---|
| `packages/trade_manager/manager.py` | Integration branch | Prevents multi-phase merge conflicts |
| `packages/trade_manager/__init__.py` | Integration branch | Public API exports change after contract review |
| `configs/risk/limits.yaml` | Phase 17 baseline | Avoids risk-limit drift during Phase 20-23 |
| `configs/execution/adapter.yaml` | Phase 19 baseline | Must remain inert and non-routable |
| `rithmic_gateway/` | CHI404 execution project only | Proprietary/live execution hot path |

## Handoff Format

Each workstream handoff must include:

1. Changed files.
2. Contract types added or changed.
3. Tests run with exact command and result.
4. Known blockers and skipped tests.
5. Confirmation that no external broker/Rithmic routing path was added.
6. GraphPost status.

## Conflict Resolution

| Conflict | Resolution |
|---|---|
| Two branches need `manager.py` | Move both changes to integration branch |
| Two branches need same config | Create a contract field in `PHASE_CONTRACTS.md` first |
| Test count drift | Phase 25 updates validation matrix before integration merge |
| Adapter/routing question | Stop and require explicit CHI404 execution approval |
