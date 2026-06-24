# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent project requirements outside that authority.

# Canonical Project Plan

Status: v0.1 planning-control artifact. This document defines the target
product and control boundaries. It is not evidence that the repo currently
implements every target behavior.

## Product Vision

HFT3 should become a professional-grade quantitative research and
model-validation platform for CME futures and CME options:

> An autonomous market-research cockpit that discovers, tests, validates,
> rejects, and tracks high-frequency trading models using real market data,
> macro-event context, volatility data, options data, and strict mathematical
> robustness gates.

The finished system should feel like a research terminal for CME microstructure
strategies: a user can open the cockpit and see what research is running, which
models are being tested, which events they target, which contextual features
they use, what data is missing, what failed, what passed, what is robust, what
is blocked, and what models are eligible for future trading consideration.

## Current Planning Position

The repo is a meaningful scaffold with research plumbing, workbench/cockpit
surfaces, validation ideas, graph/vault workflow, and partial lifecycle pieces.
It is not yet the complete product-box system described above.

The plan must therefore separate:

| Category | Meaning |
|---|---|
| Target product behavior | What the finished product must be able to do. |
| Current implementation | What the repo actually does today. |
| Controlled gap | A planned feature with literature basis, data boundary, tests, and acceptance gate. |
| Unsupported idea | A plausible idea that has not passed the planning standard. |

## System Boundaries

| Boundary | Rule |
|---|---|
| Market scope | CME futures and CME options. Historical crypto/equities work is outside this repo after lane split unless explicitly restored by owner decision. |
| Live/paper topology | CHI404 bare metal only. The Windows workstation is offline research, tests, docs, and cockpit observation. |
| Research ontology | Use existing workbench, replay, event universe, certification, and artifact objects. Do not create a second pipeline when an ontology object already exists. |
| Data truth | Real market data, real event timestamps, PIT joins, and explicit missing-data states. Smoke, fixture, stale, or structural-only evidence must not be shown as production-ready. |
| Model promotion | No model is promoted from in-sample performance alone. Promotion requires robustness gates and rejection reasons. |
| Cockpit role | Observability and audited controls. The cockpit reflects backend state; it must not invent green status. |

## Required Product Capabilities

### 1. Autonomous Research Engine

Continuously runs research without LLM babysitting, using existing durable job
and artifact boundaries. It produces artifacts, reports, and pass/fail decisions.

Acceptance: a run can start from clean state, execute the selected model/event
scope, write standardized artifacts, and expose queue/run state to the cockpit.

### 2. Full Model Universe Testing

Supports a managed library of many models rather than one-off experiments. Each
model is tested across symbols, macro events, latency assumptions, and regimes.

Acceptance: every model receives a lifecycle status and rejection/promotion
reason; missing coverage is explicit.

### 3. Macro Event Intelligence

Measures behavior around scheduled volatility events such as CPI, NFP, FOMC,
PPI, PCE, GDP, and unemployment claims.

Acceptance: event windows, release timestamps, tradable timestamps, pre-event
information boundaries, and leakage controls are explicit.

### 4. Context Feature System

Uses macro context, VIX/volatility data, CME options behavior, and cross-market
behavior as features. The term is feature, not "clue."

Acceptance: the system measures target-only baseline versus context-enhanced
performance and rejects any feature whose PIT availability is not proven.

### 5. First-Class CME Options Lane

CME options are not merely side data for futures. Options models require their
own data readiness, model lifecycle, robustness testing, artifact schema, and
dashboard visibility.

Acceptance: options models can be researched and rejected/promoted on their own
terms, while options-derived features can separately feed CME futures models.

### 6. Robustness / Anti-Overfit Gauntlet

Every model must pass walk-forward validation, PBO/CSCV-style controls,
bootstrap confidence, fee/slippage stress, latency stress, sample-size checks,
and multiple-testing controls before promotion.

Acceptance: no green/promotion state is possible without valid robustness
artifacts and explicit thresholds.

### 7. Evidence-Based Cockpit

The dashboard shows backend truth: active runs, model status, data readiness,
blockers, robustness state, lifecycle stage, and artifact freshness.

Acceptance: every panel maps to an object, file, run, gate, queue state, or
state transition; stale evidence is labeled stale.

### 8. Model Library And Lifecycle

Successful and failed models live in a managed library with statuses such as
candidate, testing, rejected, promoted candidate, observed, degraded, and retired.

Acceptance: every status has a reason, source artifacts, and timestamped
provenance.

### 9. Clean Reproducibility

Every serious research run starts from a clean active-run boundary. Prior
artifacts cannot contaminate new research.

Acceptance: fresh-start manifest, active run id, artifact reuse policy, and
leakage-detection output exist before all-model/all-lane research.

### 10. Finance-Grade Correctness

Every number has provenance. No hidden state, unmeasured claim, unsupported
green status, or casual decimal/threshold change is accepted.

Acceptance: feature records, traceability rows, tests, artifacts, and rejection
rules all agree.

## Required Workflow

```text
Plan feature -> classify feature -> map literature/ontology -> define data/PIT
-> define backend behavior -> define cockpit behavior -> define tests
-> implement smallest slice -> local preflight -> review -> verify
-> Plan Drift Review -> Review Surface Gate -> PR GrepLoop -> update matrix
```

## Non-Goals

```text
Generic quant dashboard
Decorative cockpit without backend truth
Loose notebook collection
LLM-generated trading ideas without literature and tests
Second pipeline duplicating existing workbench/replay ontology
Live or paper order routing from the workstation
Model promotion from fixture, smoke, stale, or in-sample-only evidence
```

## Planning Authority

- [PROJECT_PLANNING_STANDARD.md](PROJECT_PLANNING_STANDARD.md)
- [FEATURE_LITERATURE_TRACEABILITY_MATRIX.md](FEATURE_LITERATURE_TRACEABILITY_MATRIX.md)
- [ACCEPTANCE_CHECKLIST.md](ACCEPTANCE_CHECKLIST.md)
- [OPEN_QUESTIONS_AND_REJECTIONS.md](OPEN_QUESTIONS_AND_REJECTIONS.md)
- [../START_HERE.md](../START_HERE.md)
- [../references/README.md](../references/README.md)
- [../references/MANIFEST.md](../references/MANIFEST.md)
- Vault: `wiki/hot.md`, `Home.md`, `Memory Stack.md`, `library/System Implications.md`, `library/papers/Papers MOC.md`
