# Documentation index (read in this order)

Use this list when onboarding or explaining the repo to another developer. Each step builds on the previous one.

| Step | Document | Why read it |
|------|----------|-------------|
| 1 | [GETTING_STARTED.md](GETTING_STARTED.md) | **Start here** — clone, setup, lanes, verification |
| 2 | [../BLUEPRINT.md](../BLUEPRINT.md) | System spec: math invariants, topology, walk-forward |
| 3 | [references/README.md](references/README.md) + [references/MANIFEST.md](references/MANIFEST.md) | Authority PDF bundle and citation map |
| 4 | [REVIEWER_CHARTER.md](REVIEWER_CHARTER.md) | Non-negotiable math and review gates |
| 5 | [vault/RESEARCH_ENTRYPOINTS.md](vault/RESEARCH_ENTRYPOINTS.md) | Offline research commands (Databento → replay) |
| 6 | [workbench/README.md](workbench/README.md) | Workbench lane: campaigns, latency, after-action |
| 7 | [rithmic_trial/README.md](rithmic_trial/README.md) | Quarantined live capture (CHI404 only) |
| 8 | [GRAPHIFY_WORKFLOW.md](GRAPHIFY_WORKFLOW.md) | Code graph: AST rebuild + optional local semantic |
| 9 | [../AGENTS.md](../AGENTS.md) + [AGENTIC_ENGINEERING.md](AGENTIC_ENGINEERING.md) | Agent delegation and verify loop |

## Subsystem deep dives (after the path above)

| Topic | Doc |
|-------|-----|
| Workbench latency authority | [workbench/LATENCY_ARCHITECTURE.md](workbench/LATENCY_ARCHITECTURE.md) |
| Workbench memory authority | [workbench/MEMORY_ARCHITECTURE.md](workbench/MEMORY_ARCHITECTURE.md) |
| Hot-memory universe (market-state) | [workbench/HOT_MEMORY_UNIVERSE.md](workbench/HOT_MEMORY_UNIVERSE.md) |
| CHI404 memory upgrade (PDF §2–3 gap-fill) | [chi404/MEMORY_UPGRADE.md](chi404/MEMORY_UPGRADE.md) |
| Model catalog + composition | [workbench/MODEL_CATALOG.md](workbench/MODEL_CATALOG.md) |
| Walk-forward campaigns | [workbench/WALK_FORWARD_CAMPAIGNS.md](workbench/WALK_FORWARD_CAMPAIGNS.md) |
| After-action LLM (post-run) | [workbench/AFTER_ACTION_REPORTS.md](workbench/AFTER_ACTION_REPORTS.md) |
| Grader checklist | [workbench/GRADER_CHECKLIST.md](workbench/GRADER_CHECKLIST.md) |
| Structural PDF models | [structural_models/PDF_MODELS.md](structural_models/PDF_MODELS.md) |
| Audit remediation | [AUDIT_FRICTION_REPORT.md](AUDIT_FRICTION_REPORT.md) |

## Vendor submodules

| Path | Upstream |
|------|----------|
| `vendor/openfoundry/` | [syzygyhack/open-foundry](https://github.com/syzygyhack/open-foundry) |
| `vendor/alphageometry/` | [google-deepmind/alphageometry](https://github.com/google-deepmind/alphageometry) |

Pins: [integrations/openfoundry/VENDOR.lock](../integrations/openfoundry/VENDOR.lock)
