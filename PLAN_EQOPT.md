# Plan: Operational Equities + Options Lane Integrity

**Status:** In progress
**Scope:** `packages/equities_lane/`, `data/equities/`, `data/options/`, related docs, workbench status display, ontology extension for equities/options concepts.

## Problem statement

The equities lane is operational (13 decadal sessions, real equity MBO on disk,
3 sessions with normalized OPRA options). The previous code path:

- Read OPRA NDJSON but did not feed it into the feature pipeline.
- Decided stock vs no-trade only; never computed option EV, combined EV, or
  stock+option hedge.
- Did not enforce point-in-time filtration on either equity or option side.
- Did not reject contaminated runs.
- Had `allow_degraded=True` in the audit script (gate bypass) — fixed in
  `d0879ad`.
- Had a 5MB "skip-large" optimization in the audit script (data cherry-pick
  shortcut) — being removed here.
- `AGENTS.md` and `docs/research/LOW_FLOAT_RUNNER.md` still label the lane
  "quarantined." This is doc drift relative to the operational status; the
  lane is operational and we are not adding gates that block it.

## Required outputs

1. Stock / option / stock+option / no-trade route comparison for every
   candidate decision, with point-in-time filtration on both sides.
2. Per-session report containing data lineage, coverage, route comparison
   table, final route, leakage status, ontology/citation IDs.
3. Updated AGENTS.md and doc files to reflect operational status (data
   isolation invariant preserved, no new gates that block clean runs).
4. Ontology objects for equities/options decision context (small extension,
   not a redesign).
5. Tests covering all four routes, leakage rejection, UI/backend parity.

## Hard rules

- No new global gates that block the lane.
- No graphify gates (GraphGate, GraphPre, GraphPost) added.
- No redesign of the backtester beyond what's needed to emit the route
  comparison record.
- No CME futures production changes unless an equities/options read-only
  compatibility fix requires it.
- Quarantine invariant preserved: equities NPZ writes to
  `data/equities/npz/` only, options raw to `data/options/equity_chains/`,
  no writes to production CME `data/npz/`.
- L3-only policy preserved. `allow_degraded=False` everywhere in production
  research paths. Synthetic fixture remains degraded for unit tests only.

## Source-of-truth documents (do not edit against)

- `AGENTS.md` — lane status table (will be reconciled)
- `docs/research/LOW_FLOAT_RUNNER.md` — operational runbook
- `docs/research/EQUITY_OPTIONS_DATA_MAP.md` — paths
- `packages/equities_lane/config/universe.yaml` — universe
- `packages/equities_lane/config/decadal_runners.yaml` — decadal catalog
- `data/equities/manifest/session_bundle_v2.json` — factual join manifest
- `data/equities/metadata/float_pit.csv` — float metadata (point-in-time)

## Architecture (smallest-edit)

```
                 ┌──────────────────────┐
                 │  equity NDJSON       │──►┐
                 │  (MBO L3)            │   │
                 └──────────────────────┘   │   ┌─────────────────────┐
                                            ├──►│  Point-in-time      │
                 ┌──────────────────────┐   │   │  filter             │
                 │  options NDJSON      │──►┘   │  (ts <= decision)   │
                 │  (OPRA cbbo-1m)      │       └──────────┬──────────┘
                 └──────────────────────┘                  │
                                                            ▼
                                                  ┌──────────────────┐
                                                  │ Feature builder  │
                                                  │ (equity + option)│
                                                  └────────┬─────────┘
                                                           ▼
                                                  ┌──────────────────┐
                                                  │ Route comparison │
                                                  │ stock / option / │
                                                  │ combo / no-trade │
                                                  └────────┬─────────┘
                                                           ▼
                                                  ┌──────────────────┐
                                                  │ Per-session      │
                                                  │ report +         │
                                                  │ decision record  │
                                                  └──────────────────┘
```

## Steps (full list in CHECKLIST_EQOPT.md)

1. Doc drift reconciliation (AGENTS.md, LOW_FLOAT_RUNNER.md).
2. Ontology objects for equities + options (small extension pack).
3. Point-in-time leakage filter.
4. Stock / option / combo / no-trade route comparison.
5. Float metadata as_of check.
6. Workbench status display.
7. Tests (route reachability, leakage rejection, doc parity).
8. Run full operational experiment and produce engineering report.

## Acceptance

- Lane remains operational; no global blockers added.
- Routes STOCK_ONLY, OPTION_ONLY, STOCK_AND_OPTION, NO_TRADE are all reachable
  in controlled tests.
- Contaminated runs are rejected with a precise reason; clean runs continue.
- Workbench reflects operational status.
- Final engineering report lists files changed, tests run, remaining gaps.
