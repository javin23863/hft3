# MANDATORY ONTOLOGY GATE: Before every interaction in this project, operate from the Obsidian vault ontology and the provided mathematics/quantitative-finance/HFT PDFs; do not invent codebases, pipelines, models, or methodology outside that authority.

# Start Here

This is the chronological path for a human developer or agent entering `hft3`.
It does not replace the detailed docs; it tells you what to read and run first.

## 0. Memory And Authority

Read these before asking the owner for context or changing code:

1. Vault `wiki/hot.md`
2. Vault `Home.md`
3. Vault `Memory Stack.md`
4. [AGENTS.md](../AGENTS.md)
5. [BLUEPRINT.md](../BLUEPRINT.md)
6. [docs/project/PROJECT_PLANNING_STANDARD.md](project/PROJECT_PLANNING_STANDARD.md)
7. [docs/project/Q001_OWNER_DECISION_PACKET.md](project/Q001_OWNER_DECISION_PACKET.md)
8. [docs/project/Q001_DATA_INVENTORY_STATUS.md](project/Q001_DATA_INVENTORY_STATUS.md)
9. [docs/references/README.md](references/README.md)

Non-negotiables: filtration, event-time ordering, walk-forward discipline,
CHI404-only live/paper topology, and quarantine boundaries.

## 1. Repository Orientation

Use this order:

1. [docs/human/GETTING_STARTED.md](human/GETTING_STARTED.md)
2. [docs/human/DOC_INDEX.md](human/DOC_INDEX.md)
3. [docs/REPO_MAP.md](REPO_MAP.md)
4. [docs/human/RUNTIME_CONTRACT.md](human/RUNTIME_CONTRACT.md)
5. [docs/ai/ONBOARDING.md](ai/ONBOARDING.md) for agent graph workflow

The short version:

- `apps/` contains runnable applications, including cockpit and workbench.
- `packages/` contains the reusable research, validation, data, and lane code.
- `scripts/` contains operational and research entrypoints.
- `artifacts/` and `research_cards/` contain generated research outputs.
- `runtime/` contains machine-local state, validation, latency, and audit outputs.
- `graphify-out/` is the code graph for agent navigation.

## 2. Fresh-State Rule

Before any all-model or all-lane research run, reset the active generated-artifact
boundary:

Precondition: before any all-model/all-lane research or fresh-start run, check
Q001. Current owner decision is `ACCEPTED_AVAILABLE_DATA_SCOPE`: available-data
models may run with explicit coverage, skip, or rejection reasons. Models that
require missing MBO slots or strict options quote reconstruction stay sidelined
until data is filled or separately scoped out.

```powershell
$env:PYTHONPATH = "apps;packages"
python -m apps.workbench fresh-start --confirm-hard-delete --json
python -m apps.workbench leakage-detect --json
```

This removes untracked generated evidence from the configured cleanup roots,
writes a pre-delete manifest, creates `runtime/workbench/active_run.json`, and
sets `artifact_reuse_policy=active_run_id_only`.

Tracked historical artifacts are not deleted. They are listed in the rejected
stale-artifact ledger and must not be treated as active-run evidence.

Reference: [docs/LEAKAGE_DETECTION.md](LEAKAGE_DETECTION.md).

## 3. Research Lifecycle

Use the existing pipeline only:

1. Planning standard: [docs/project/PROJECT_PLANNING_STANDARD.md](project/PROJECT_PLANNING_STANDARD.md)
2. Canonical project plan: [docs/project/CANONICAL_PROJECT_PLAN.md](project/CANONICAL_PROJECT_PLAN.md)
3. Feature traceability matrix: [docs/project/FEATURE_LITERATURE_TRACEABILITY_MATRIX.md](project/FEATURE_LITERATURE_TRACEABILITY_MATRIX.md)
4. Event universe: [docs/vault/ECONOMIC_EVENT_UNIVERSE.md](vault/ECONOMIC_EVENT_UNIVERSE.md)
5. Entry commands: [docs/vault/RESEARCH_ENTRYPOINTS.md](vault/RESEARCH_ENTRYPOINTS.md)
6. Workbench campaigns: [docs/workbench/README.md](workbench/README.md)
7. Certification: [docs/vault/BACKTESTER_CERTIFICATION.md](vault/BACKTESTER_CERTIFICATION.md)
8. Validation honesty: [docs/VALIDATION_HONESTY.md](VALIDATION_HONESTY.md)

Do not create a second pipeline, event catalog, feature schema, or promotion path
when the existing ontology object covers the behavior.

## 4. Cockpit And Current State

Cockpit is an observer over artifacts and gates, not a trading shortcut.

Read in order:

1. [docs/cockpit/BUILDOUT_REVIEW.md](cockpit/BUILDOUT_REVIEW.md)
2. [docs/cockpit/BUILDOUT_CORRECTNESS_CHECKLIST.md](cockpit/BUILDOUT_CORRECTNESS_CHECKLIST.md)
3. [docs/cockpit/CME_M6_SWEEP_CONTROL_PLAN.md](cockpit/CME_M6_SWEEP_CONTROL_PLAN.md)
4. [docs/cockpit/MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md](cockpit/MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md)

Options lane status is first-class but shadow/live remains blocked until the
options defect ledger clears. CME futures research may use options as context
only through point-in-time measured features, not raw lake existence.

## 5. Verification Order

For source changes, use the scope-specific tests first, then widen:

```powershell
python -B -m pytest -q tests\test_workbench\test_fresh_start.py -p no:cacheprovider
python -B -m pytest -q apps\cockpit\backend\tests\test_cockpit.py -p no:cacheprovider
git diff --check
```

Use [docs/REVIEWER_CHARTER.md](REVIEWER_CHARTER.md) and
[docs/VALIDATION_HONESTY.md](VALIDATION_HONESTY.md) before calling anything
merge-ready.

## 6. Live/Paper Boundary

The workstation is offline research only. CHI404 is the CME live/paper host.

Read before any Rithmic, latency, or infra work:

1. [docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md](vault/CHI404_CANONICAL_ENTRYPOINTS.md)
2. [docs/rithmic_trial/README.md](rithmic_trial/README.md)
3. [docs/LATENCY_BASELINE.md](LATENCY_BASELINE.md)
4. [specs/LATENCY.md](../specs/LATENCY.md)
