# HFT3 Project Roadmap

This roadmap turns the remaining HFT3 phases into parallel workstreams with clear boundaries. It is the coordination layer for phases 20-25 and for future CHI404-only execution routing.

## Current Status

| Area | Status | Notes |
|---|---|---|
| Phases 1-13 | Complete | Autonomous/research pipeline scaffold, gates, registry, artifacts, reporting |
| Phases 14-21 | Complete | Trade Manager handoff through inert kill-switch decisions |
| Phase 22 | Complete | Read-only local artifact observer CLI |
| Phase 23 | Complete | Inert session reporting artifacts |
| Phase 24 | Complete | Autonomous-runner checkpoint recovery is hardened and tested; Trade Manager restart recovery remains future work |
| Phase 25 | Partial | Original required-test matrix remains incomplete |

## Non-Negotiable Constraints

1. Dev workstation must not route live, paper, or Rithmic orders.
2. `rithmic_gateway/`, C++ hot path, and CHI404 execution paths stay untouched unless explicitly authorized.
3. Phase 19 remains an inert boundary until a separate CHI404-only execution project is approved.
4. Each phase starts as an isolated module with tests before shared manager integration.
5. Shared `TradeManager` wiring happens only on the integration branch.

## Workstream Waves

| Wave | Workstreams | Goal | Merge Target |
|---|---|---|---|
| 1 | Phase 20, Phase 25 | Position/reconciliation contract and validation matrix hardening | `integration/trade-manager-20-23` |
| 2 | Phase 21, Phase 23 | Kill-switch decisions and session artifact contracts | `integration/trade-manager-20-23` |
| 3 | Phase 22, Phase 24 | Read-only observer and autonomous resumability hardening | `integration/trade-manager-20-23` for observer, separate validation branch for Phase 24 |
| 4 | Integration | Wire approved modules into Trade Manager without routing | `main` |

## Phase Targets

| Phase | Deliverable | Done Means |
|---|---|---|
| 20 | `packages/trade_manager/monitor.py` | Position snapshots and reconciliation decisions are tested and inert |
| 21 | `packages/trade_manager/kill_switch.py` | 12 trigger families and configured actions are tested and inert |
| 22 | `apps/observer/` | CLI reads session state and cannot mutate or route |
| 23 | `packages/trade_manager/session.py` | 16 documented session artifacts are written and validated |
| 24 | autonomous-runner resumability and failure safety | Existing checkpoint flow is hardened and crash recovery is tested |
| 25 | required-test closure and validation docs/scripts | Required gate matrix is explicit, runnable, and honest about blockers |

## Dependency Rules

| Producer | Consumers | Contract |
|---|---|---|
| Phase 20 position monitor | Phase 21, Phase 22, Phase 23 | `PositionSnapshot`, `PositionReconciliationResult` |
| Phase 21 kill switch | Phase 22, Phase 23 | `KillSwitchDecision`, `KillSwitchEvent` |
| Phase 23 session reporting | Phase 22, future Trade Manager restart work | session manifest and JSONL artifact schemas |
| Phase 25 validation matrix | all phases | commands, expected counts, blockers |

## Branch Plan

| Branch | Owner Scope |
|---|---|
| `phase20-position-monitor` | Phase 20 module/tests/docs only |
| `phase21-kill-switch` | Phase 21 module/config/tests/docs only |
| `phase22-observer-cli` | Observer CLI/tests/docs only |
| `phase23-session-reporting` | Session writer/schema/tests/docs only |
| `phase24-resumability-safety` | Autonomous-runner resumability/crash-recovery tests and docs only |
| `phase25-required-tests` | Required-test closure, validation scripts, docs only |
| `integration/trade-manager-20-23` | Shared `TradeManager` integration and cross-phase tests only |

## Merge Order

1. Merge phase contracts and isolated modules first.
2. Merge Phase 20 before Phase 21 runtime integration.
3. Merge Phase 23 before Phase 22/24 rely on session artifacts.
4. Merge Phase 25 validation updates continuously.
5. Merge integration branch only after all scoped and cross-phase gates pass.
