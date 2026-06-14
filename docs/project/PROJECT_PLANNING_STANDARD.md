# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent project requirements outside that authority.

# Project Planning Standard: Literature-Traceable Feature Control

Status: v0.1 planning-control standard. This is not an implementation prompt and
does not claim the current repo already satisfies the product vision.

## Purpose

This document defines how HFT3 is planned, evaluated, and controlled.

It is not a feature request. It is not a brainstorming document. It is the
standard used to decide whether a feature belongs in the project, how that
feature should be defined, what evidence supports it, and how future work is
checked against the project end goal.

The project must be planned from first principles, grounded in academic
literature, the HFT3 vault ontology, and testable system behavior. No feature
enters the roadmap because it sounds useful, appears common in trading systems,
or was suggested by an LLM without evidence.

## Planning Objective

The planning process exists to keep the system aligned with this end goal:

> Build a research and model-testing system that can evaluate a full universe of
> trading models against historical CME futures and CME options data, discover
> real edge around volatility and macro-event conditions, separate independently
> profitable models from contextual feature models, enforce strict robustness
> testing, and expose the true backend state through a cockpit/dashboard.

Every planning decision must answer:

1. Why does this feature exist?
2. What part of the end goal does it support?
3. What academic literature, formal ontology, or accepted technical framework supports it?
4. What system behavior must exist for the feature to be real?
5. What evidence proves the feature works?
6. What failure condition rejects, defers, or marks the feature experimental?

## Core Planning Rule

No feature enters the roadmap unless it has:

```text
Feature thesis
End-goal connection
Literature or ontology basis
Data requirement
Implementation boundary
Testable behavior
Acceptance gate
Failure/rejection rule
```

A feature without these elements is only an idea.

## Feature Classification

Every feature must be classified before it is treated as part of the system.

```text
SUPPORTED
PARTIALLY_SUPPORTED
EXPERIMENTAL
UNSUPPORTED
REJECTED
```

| Classification | Meaning |
|---|---|
| SUPPORTED | Clear role, strong literature/ontology basis, data requirements, measurable acceptance criteria. |
| PARTIALLY_SUPPORTED | Some literature or technical support exists, but implementation, data, or acceptance criteria are incomplete. |
| EXPERIMENTAL | Plausible but not proven; may be tested but must not be treated as production logic. |
| UNSUPPORTED | Lacks sufficient theoretical, empirical, or technical basis; not a core feature. |
| REJECTED | Conflicts with the project goal, creates leakage risk, duplicates functionality, or cannot be tested. |

## Required Feature Record

Every accepted or candidate feature must use this structure:

```text
Feature ID:
Feature Name:
Feature Classification:
Source / Origin:
End-Goal Connection:
Feature Thesis:
Problem It Solves:
Required System Behavior:
Inputs:
Outputs:
Data Requirements:
Point-in-Time / Leakage Requirements:
Academic or Ontological Basis:
Primary Literature:
Secondary Literature:
Implementation Boundary:
Backend State Requirement:
Dashboard / Cockpit Requirement:
Required Tests:
Acceptance Gate:
Failure Modes:
Rejection Rule:
Open Questions:
```

## Literature Traceability Standard

The literature is not decorative. It determines whether the feature is
legitimate, how it should behave, and what test is required.

| Feature domain | Required basis | Local authority |
|---|---|---|
| Event-based trading | Event-study methodology, price adjustment, announcement effects, volatility response, event windows. | Vault `library/papers/`; `docs/vault/ECONOMIC_EVENT_UNIVERSE.md`; `docs/references/MANIFEST.md` `event_context`. |
| Market microstructure | Limit order book theory, order-flow imbalance, liquidity provision, adverse selection, queue dynamics, price impact. | Vault `library/System Implications.md`; `docs/references/chicago_cme_microstructure_mathematical_model.pdf`; `docs/research/MBO_FEATURE_PACKET_SOURCE_OF_TRUTH.md`. |
| Robustness testing | Out-of-sample validation, walk-forward analysis, multiple-testing control, data-snooping, PBO/CSCV, Reality Check / SPA-style controls. | `docs/vault/BACKTESTER_CERTIFICATION.md`; `docs/references/Ultimate_Quantitative_Finance_Researcher.pdf`; `docs/VALIDATION_HONESTY.md`. |
| Point-in-time data | Temporal data design, vintage/as-of joins, leakage prevention, auditability, reproducible research. | `docs/LEAKAGE_DETECTION.md`; `docs/START_HERE.md` fresh-state rule; `docs/references/MANIFEST.md`. |
| Model comparison | Independently tradable strategies vs contextual features, regime filters, defensive overlays, rejected models. | `docs/workbench/MODEL_CATALOG.md`; `docs/cockpit/MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md`; vault `System Implications`. |
| Dashboard/cockpit | Backend truth, artifact freshness, state transitions, gate outcomes, explicit human action. | `docs/human/RUNTIME_CONTRACT.md`; `docs/VALIDATION_HONESTY.md`; `docs/cockpit/BUILDOUT_CORRECTNESS_CHECKLIST.md`. |
| CME options | Options microstructure, hedging/liquidity spillovers, option order imbalance, fee/tick rules, PIT options chains. | Vault `library/papers` category 11; `docs/ops/ws0-3-cme-options-fees.md`; `docs/ops/ws0-5-tick-table-status.md`. |

## Planning Boundaries

The system must not become:

```text
A generic quant dashboard
A collection of disconnected tabs
A loose research notebook
A trading idea scrapbook
A black-box LLM research assistant
A system that promotes models without robustness testing
A UI that does not reflect backend truth
A framework where unsupported ideas silently become features
```

## Acceptance Standard

A feature is accepted only when all are true:

```text
The feature has a clear thesis.
The feature supports the stated end goal.
The feature has a literature or ontology basis.
The required data is identified.
Point-in-time rules are defined.
Leakage risks are identified.
Backend behavior is defined.
Dashboard behavior is defined, if applicable.
Tests are defined.
Acceptance criteria are defined.
Failure modes are defined.
Rejection rules are defined.
```

## Controlled Planning Artifacts

| Artifact | Purpose |
|---|---|
| [CANONICAL_PROJECT_PLAN.md](CANONICAL_PROJECT_PLAN.md) | Mission, end goal, system boundaries, workflows, required feature areas, acceptance gates, and non-goals. |
| [FEATURE_LITERATURE_TRACEABILITY_MATRIX.md](FEATURE_LITERATURE_TRACEABILITY_MATRIX.md) | Maps every major feature to literature/ontology, data, implementation boundaries, tests, and acceptance gates. |
| [ACCEPTANCE_CHECKLIST.md](ACCEPTANCE_CHECKLIST.md) | Checklist used before future development continues. |
| [OPEN_QUESTIONS_AND_REJECTIONS.md](OPEN_QUESTIONS_AND_REJECTIONS.md) | Tracks unresolved planning questions, unsupported ideas, experimental items, and rejected concepts. |

## Governing Rule

HFT3 is not planned by intuition, convenience, or general LLM reasoning.

It is planned through:

```text
End-goal alignment
Literature traceability
Point-in-time correctness
Backend observability
Testable behavior
Robustness validation
Explicit rejection rules
```

Anything outside that standard is not part of the controlled project plan.
