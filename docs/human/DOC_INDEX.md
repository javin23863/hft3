# Documentation index (human — read in this order)

Chronological onboarding for human developers. AI agents: [../ai/ONBOARDING.md](../ai/ONBOARDING.md) (graph-first).

| Step | Document | Why read it |
|------|----------|-------------|
| 0 | [../REPO_STATE.md](../REPO_STATE.md) | Canonical path (`C:\Users\MSI\repos\hft3`), active `main`, branch map, clean-tree checks |
| **0a** | **[RESEARCH_SYSTEM_EXECUTION_ORDER.md](RESEARCH_SYSTEM_EXECUTION_ORDER.md)** | **Chronological research path: data → features → families → HftBacktest-only run → post-HBT evaluation → learning → artifacts** |
| **0b** | **[../project/FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md](../project/FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md)** | **Feature-family inventory, canonical vs obsolete paths, status manifest** |
| **0c** | **[../project/FEATURE_FAMILY_RESEARCH_SYSTEM_PROMPT.md](../project/FEATURE_FAMILY_RESEARCH_SYSTEM_PROMPT.md)** | **Active workstream phases 0–9 (integration + ordering)** |
| 1 | [GETTING_STARTED.md](GETTING_STARTED.md) | Clone, setup, lanes, verification |
| 2 | [../../BLUEPRINT.md](../../BLUEPRINT.md) | System spec: math invariants, topology, walk-forward |
| 3 | [../references/README.md](../references/README.md) + [../references/MANIFEST.md](../references/MANIFEST.md) | Authority PDF bundle |
| 4 | [../REVIEWER_CHARTER.md](../REVIEWER_CHARTER.md) | Non-negotiable review gates |
| 4a | [../VALIDATION_HONESTY.md](../VALIDATION_HONESTY.md) | Scope-green verify; no fake PASS or completed todos |
| 5 | [../vault/RESEARCH_ENTRYPOINTS.md](../vault/RESEARCH_ENTRYPOINTS.md) | Offline research commands |
| 5a | [../vault/ECONOMIC_EVENT_UNIVERSE.md](../vault/ECONOMIC_EVENT_UNIVERSE.md) | Macro calendar catalog, timezones, snapshots |
| 5b | [../vault/BACKTESTER_CERTIFICATION.md](../vault/BACKTESTER_CERTIFICATION.md) | T0–T4 certification tiers |
| 5c | [../research/MBO_FEATURE_PACKET_SOURCE_OF_TRUTH.md](../research/MBO_FEATURE_PACKET_SOURCE_OF_TRUTH.md) | Canonical MBO feature packet math and machine contract |
| 5d | [../agents/MBO_AGENT_ONTOLOGY_HARDENING_SOURCE_OF_TRUTH.md](../agents/MBO_AGENT_ONTOLOGY_HARDENING_SOURCE_OF_TRUTH.md) | Source-of-truth doctrine for LLM agent ontology and schema hardening |
| 6 | [../workbench/README.md](../workbench/README.md) | Workbench campaigns, latency, after-action |
| 7 | [../rithmic_trial/README.md](../rithmic_trial/README.md) | Quarantined live capture (CHI404 only) |
| 7a | [../chi404/HARDWARE_BASELINE.md](../chi404/HARDWARE_BASELINE.md) | CHI404 CPU/memory/NIC baseline + verify gates |
| 7b | [../chi404/CPU_MEMORY_OVERCLOCK.md](../chi404/CPU_MEMORY_OVERCLOCK.md) | UEFI EXPO/PBO + market-load stability |
| 8 | [../GRAPHIFY_WORKFLOW.md](../GRAPHIFY_WORKFLOW.md) | Code graph rebuild |
| 9 | [../../AGENTS.md](../../AGENTS.md) + [../AGENTIC_ENGINEERING.md](../AGENTIC_ENGINEERING.md) | Agent delegation |
| 10 | [../project/PROJECT_PLANNING_STANDARD.md](../project/PROJECT_PLANNING_STANDARD.md) + [../project/CANONICAL_PROJECT_PLAN.md](../project/CANONICAL_PROJECT_PLAN.md) + [../project/FEATURE_LITERATURE_TRACEABILITY_MATRIX.md](../project/FEATURE_LITERATURE_TRACEABILITY_MATRIX.md) | Project planning doctrine, target product plan, and feature traceability |
| 10a | [../project/ACCEPTANCE_CHECKLIST.md](../project/ACCEPTANCE_CHECKLIST.md) + [../project/OPEN_QUESTIONS_AND_REJECTIONS.md](../project/OPEN_QUESTIONS_AND_REJECTIONS.md) | Planning gates, unresolved questions, and rejected ideas |
| 10b | [../project/ROADMAP.md](../project/ROADMAP.md) + [../project/WORKSTREAMS.md](../project/WORKSTREAMS.md) | Parallel phase execution plan |
| 11 | [../REPO_MAP.md](../REPO_MAP.md) | Complete top-level directory map |
| 12 | [RUNTIME_CONTRACT.md](RUNTIME_CONTRACT.md) | Backend ↔ UI artifact schema |

## Subsystem deep dives

| Topic | Doc |
|-------|-----|
| Workbench latency | [../workbench/LATENCY_ARCHITECTURE.md](../workbench/LATENCY_ARCHITECTURE.md) |
| Model catalog | [../workbench/MODEL_CATALOG.md](../workbench/MODEL_CATALOG.md) |
| After-action LLM | [../workbench/AFTER_ACTION_REPORTS.md](../workbench/AFTER_ACTION_REPORTS.md) |
| Project merge protocol | [../project/MERGE_PROTOCOL.md](../project/MERGE_PROTOCOL.md) |
| Phase contracts | [../project/PHASE_CONTRACTS.md](../project/PHASE_CONTRACTS.md) |
| Feature-family audit | [../project/FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md](../project/FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md) |
| Feature-family status YAML | [../project/FEATURE_FAMILY_STATUS_MANIFEST.yaml](../project/FEATURE_FAMILY_STATUS_MANIFEST.yaml) |
| Research execution order | [RESEARCH_SYSTEM_EXECUTION_ORDER.md](RESEARCH_SYSTEM_EXECUTION_ORDER.md) |
| Validation matrix | [../project/VALIDATION_MATRIX.md](../project/VALIDATION_MATRIX.md) |
| Structural PDF models | [../structural_models/PDF_MODELS.md](../structural_models/PDF_MODELS.md) |
| Contributing | [../../CONTRIBUTING.md](../../CONTRIBUTING.md) |
