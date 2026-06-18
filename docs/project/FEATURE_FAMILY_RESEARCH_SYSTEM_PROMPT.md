# Developer Prompt: Complete and Chronologically Organize the Feature-Family Research System

**Status:** Active workstream prompt (Phase 0–9).  
**Companion audit:** [FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md](FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md)  
**Execution order:** [../human/RESEARCH_SYSTEM_EXECUTION_ORDER.md](../human/RESEARCH_SYSTEM_EXECUTION_ORDER.md)

This is a **follow-on integration and repository-ordering assignment**.
The goal is to complete the feature-family research system already defined in `hft3`, connect all existing pieces in the correct order, remove ambiguity about which paths are active, and make the repository understandable before any large or paid backtest begins.

Do not create a separate research platform, duplicate pipeline, new scheduler, new database, new worker framework, or parallel ontology.

The repository already defines these feature families:

```text
primary_fs_v1
cross_asset_futures
vix_vvix_sensor
vix_options
cme_options_context
macro_context
continuous_session
latency_state
```

These are the canonical feature families and must remain the basis of the implementation.

The canonical research specification requires every model decision to consume or explicitly sideline each admitted family, including cross-asset state, VIX/options, earlier macro releases, continuous/session state, and latency state.

## Primary outcome

After this work, one developer should be able to follow the repository in chronological order and understand:

```text
where data enters
→ how snapshots are synchronized
→ how atomic features are created
→ how feature families are assembled
→ how feature combinations become candidates
→ how VectorBT screens them
→ how robustness gates them
→ how HftBacktest validates them
→ how completed results inform the next generation
→ where every artifact is stored
```

No one should need to search randomly across the repository to discover which pieces exist or which path is authoritative.

---

## Implementation phases (summary)

| Phase | Deliverable | Code changes |
|-------|-------------|--------------|
| 0 | Audit, execution-order doc, path table, doc index | **Docs only** |
| 1 | Family metadata contract, recipe hashing | Extend existing schemas |
| 2 | Primary + cross-asset assembly (no placeholders) | Yes |
| 3 | VIX/options-context assembly | Yes |
| 4 | Macro, session, latency families | Yes |
| 5 | VectorBT feature-recipe consumption | Yes |
| 6 | HftBacktest identical recipe handoff | Yes |
| 7 | Autonomous feature-family learning | Extend autoresearch |
| 8 | End-to-end validation | Tests + smoke |
| 9 | Paid-compute readiness | Gated launch |

Full phase requirements, tests, and definition-of-done are unchanged from the authoritative prompt issued 2026-06-18. See sections 1–23 in repository history commit introducing this file.

**Phase 0 rule:** Do not implement research code until [FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md](FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md) identifies the canonical owner of each behavior.

---

## Quick links

| Item | Path |
|------|------|
| Feature-plane contract (8 families) | `packages/backtest_pipeline/src/feature_plane.py` |
| VectorBT screening | `packages/backtest_pipeline/src/vectorbt_adapter.py` |
| HftBacktest campaign | `packages/backtest_pipeline/src/hft_campaign/` |
| Autoresearch loop | `packages/research_pipeline/generation_loop.py` |
| Feature slots | `specs/FEATURES.md` |
| Research entrypoints (CLI) | `docs/vault/RESEARCH_ENTRYPOINTS.md` |
| Paid screen runbook | `docs/project/VBT_PAID_SCREEN_RUNBOOK.md` |

---

## Required implementation order (phases 0–9)

### Phase 0 — Audit and repository ordering (complete)

- `FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md`
- Canonical/legacy path table
- `RESEARCH_SYSTEM_EXECUTION_ORDER.md`
- Updated documentation index
- **No research code changes**

### Phases 1–9

See [FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md](FEATURE_FAMILY_IMPLEMENTATION_AUDIT.md) and [RESEARCH_SYSTEM_EXECUTION_ORDER.md](../human/RESEARCH_SYSTEM_EXECUTION_ORDER.md) for phase owners. Phases 1–9 require code, tests, and manifest updates per the full specification (sections 4–22 below).

---

## Sections 4–18 (requirements summary)

- **§4** Preserve eight-family ontology in `feature_plane.py`; consumption states: `consumed`, `not_used`, `sidelined_missing_data`, `sidelined_scope`, `not_measured`.
- **§5** One causal snapshot per decision ts; reuse `sensor_feature_adapter` PIT sync.
- **§6** Replace cross-asset placeholder OFI with real leader-symbol data or fail closed.
- **§7–9** Wire VIX, macro context, latency as timestamped families with ablation.
- **§10–13** Extend existing candidate object with feature-recipe hash; freeze manifest before evaluation.
- **§14–15** VectorBT then HftBacktest consume identical frozen recipes; recipe-hash equality gate.
- **§16** Autoresearch loop order: freeze → VBT → robustness → HBT → memory → next gen.
- **§17–18** Fail closed on ambiguous defaults; maintain `FEATURE_FAMILY_STATUS_MANIFEST.yaml`.

---

## Required tests (§20)

Feature-family registry; PIT rejection; cross-asset leader proof; no zero-fill VIX; macro context separation; recipe hash determinism; VBT=HBT hash; bar-stub cannot claim feature-complete; dedup and holdout isolation; resume preserves frozen inputs; one-shot workflows remain functional.

---

## Paid-test gate (§21)

No paid run until pilot artifact proves: recipe hash, all family statuses, PIT, ablation, VectorBT, robustness, HBT handoff. `paid_screen_gate.allowed: false` in status manifest until Phase 9.

---

## Definition of done (§23)

- One chronological execution path and one canonical owner per component.
- All eight families have explicit status before testing.
- True multi-symbol data replaces placeholder cross-asset behavior.
- VectorBT and HftBacktest share frozen feature recipes with proof.
- Autonomous loop refines feature families (Phase 7), not merely four execution parameters.
- No duplicate platform/worker/DB; reuse existing runners.
- Paid testing blocked until pilot validates.
- Documentation includes exact commands in [RESEARCH_SYSTEM_EXECUTION_ORDER.md](../human/RESEARCH_SYSTEM_EXECUTION_ORDER.md).
