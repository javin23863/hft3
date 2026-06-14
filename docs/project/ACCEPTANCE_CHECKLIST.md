# MANDATORY ONTOLOGY GATE: Before using this document, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent project requirements outside that authority.

# Acceptance Checklist

Status: v0.1 planning-control artifact. Use this before a feature enters the
roadmap, before a development slice starts, and before any implementation is
called complete.

## A. Roadmap Admission

A feature may enter the controlled roadmap only if every item is answered:

```text
[ ] Feature is explicitly named.
[ ] Feature thesis is clear.
[ ] End-goal connection is stated.
[ ] Literature or ontology basis is cited.
[ ] Required data is identified.
[ ] Point-in-time and leakage rules are defined.
[ ] Required backend behavior is defined.
[ ] Dashboard/cockpit reflection is defined, if applicable.
[ ] Tests are defined.
[ ] Acceptance criteria are defined.
[ ] Failure modes are defined.
[ ] Rejection rule is defined.
[ ] Classification is SUPPORTED, PARTIALLY_SUPPORTED, EXPERIMENTAL, UNSUPPORTED, or REJECTED.
```

If any item is blank, the feature remains outside the roadmap or is classified
as EXPERIMENTAL/UNSUPPORTED.

## B. Feature Record Completeness

Every controlled feature must have:

```text
[ ] Feature ID
[ ] Feature Name
[ ] Feature Classification
[ ] Source / Origin
[ ] End-Goal Connection
[ ] Feature Thesis
[ ] Problem It Solves
[ ] Required System Behavior
[ ] Inputs
[ ] Outputs
[ ] Data Requirements
[ ] Point-in-Time / Leakage Requirements
[ ] Academic or Ontological Basis
[ ] Primary Literature
[ ] Secondary Literature
[ ] Implementation Boundary
[ ] Backend State Requirement
[ ] Dashboard / Cockpit Requirement
[ ] Required Tests
[ ] Acceptance Gate
[ ] Failure Modes
[ ] Rejection Rule
[ ] Open Questions
```

## C. Implementation Slice Start

Before coding:

```text
[ ] VaultGate completed: wiki/hot.md, Home.md, Memory Stack.md, and relevant notes.
[ ] GraphGate completed with task-specific query.
[ ] Existing ontology object checked before creating new pipeline/schema.
[ ] Feature matrix row exists or is added in this slice.
[ ] Scope is small enough for one coherent review surface.
[ ] Required tests and verification commands are named.
[ ] Live/paper topology is unaffected unless explicitly in scope.
```

## D. Point-In-Time / Leakage Gate

For any data-derived feature:

```text
[ ] Decision timestamp is defined.
[ ] Source data timestamp is defined.
[ ] Release timestamp and tradable timestamp are defined when events are used.
[ ] Vintage/as-of rule is defined when revisions or vendor histories exist.
[ ] Join rule cannot see future information.
[ ] Missing data state is explicit.
[ ] Leakage-detect or equivalent test is defined.
```

Reject the feature if the system cannot prove the data was available at the
decision timestamp.

## E. Robustness Gate

For any model, strategy, or promoted signal:

```text
[ ] Discovery, confirmation, holdout, and recent holdout boundaries are defined.
[ ] Fees and slippage are included.
[ ] Latency assumptions are included and trace to measured authority where required.
[ ] PBO/CSCV or equivalent overfit control is present.
[ ] Bootstrap/confidence or uncertainty measure is present.
[ ] Sample-size floor is present.
[ ] Multiple-testing control is present when model search is broad.
[ ] Rejection reason is written when a gate fails.
```

Reject promotion if any robustness artifact is missing, stale, malformed, or
below threshold.

## F. Cockpit Truth Gate

For every dashboard panel or status:

```text
[ ] Backend object/file/run/gate source is named.
[ ] Freshness rule is defined.
[ ] State transition source is defined.
[ ] Missing/stale/smoke/fixture states fail closed.
[ ] User-visible action or interpretation is clear.
[ ] GREEN cannot be reached from stale, fixture, structural-only, or partial evidence.
```

Reject decorative UI that does not reflect backend truth.

## G. Completion Gate

A development slice is complete only when:

```text
[ ] Local GrepLoop ran on changed scope.
[ ] PR Greptile loop ran when PR and Greptile are available, or unavailability is documented.
[ ] Reviewer found zero red findings or remaining findings are accepted blockers.
[ ] Scope-appropriate tests/builds ran with command output.
[ ] git diff --check passed.
[ ] Feature matrix and open questions/rejections are updated.
[ ] GraphPost ran when tracked graph output is affected.
[ ] merge-ready status is honest under docs/VALIDATION_HONESTY.md.
```

## H. Immediate Rejection Conditions

```text
[ ] Feature requires future information at decision time.
[ ] Feature promotes a model from in-sample results only.
[ ] Feature creates a second pipeline where an ontology object already exists.
[ ] Feature cannot be tested.
[ ] Feature cannot name required data.
[ ] Feature uses options or volatility data without PIT availability proof.
[ ] Feature changes live/paper routing through the workstation.
[ ] Dashboard status is not backed by backend state.
[ ] Feature lacks literature/ontology support and is presented as core behavior.
```
