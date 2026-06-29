# HftBacktest Campaign Architecture

Historical HftBacktest campaign modules may still document VectorBT handoff
artifacts. The active HFTBacktest-only path does not use VectorBT, Stage A
survivors, or the legacy screening artifact to decide what HBT receives. Active
HBT campaign units are determined only from canonical model registry slugs plus
HBT-normalized NPZ/event units and their admissibility metadata. The canonical
registry universe includes hypothesis, structural, and reinforcement-learning
policy/proxy slugs; legacy inventory phrases such as `50 HYP + 11 PDF` are not
active HBT eligibility definitions.

## Ontology authority (VaultGate)

Canonical scope is **not** this file alone. Ground in Obsidian vault (`wiki/hot.md`, `decisions/2026-06-17 Feature-complete research authority correction`) plus repo:

- [OPPORTUNITY_RESEARCH_SPEC.md](OPPORTUNITY_RESEARCH_SPEC.md) (when present)
- [VECTORBT_SCREENING_ENGINE_SPEC.md](VECTORBT_SCREENING_ENGINE_SPEC.md) (when present)
- [FEATURE_LITERATURE_TRACEABILITY_MATRIX.md](FEATURE_LITERATURE_TRACEABILITY_MATRIX.md)
- [MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md](../cockpit/MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md)

Every scenario declares `feature_plane_status` (`feature_complete_pit_declared` | `scheduled_event_only` | `bar_stub_research_only` | `incomplete_feature_plane`). Default campaign path: `scheduled_event_only` until full PIT feature plane is proven.

Execution-realism claims require official HftBacktest **and** hft3 native C++ hot-path evidence (vault `wiki/hot.md` 2026-06-16). Legacy/diagnostic VectorBT compute remains separate and cannot route active HBT campaign units.

## Layers

1. **Stage 0 validation** — canonical slug identity, prepared data, latency/queue/fee, source lock, authority refs
2. **Prepared data** — content-addressed `artifacts/hftbacktest_prepared_data/<hash>/`
3. **Scenario manifest** — deterministic `HftReplayScenario` rows
4. **Worker pool** — spawn processes; fresh engine per scenario; immutable caches only
5. **Artifacts** — atomic per-scenario directories under `artifacts/hftbacktest_campaigns/<campaign_id>/`
6. **Finalist combined replay** — Stage 4 only; never substitutes individual evidence

## Code layout

- `packages/backtest_pipeline/src/hft_campaign/` — runner, worker, manifest, prepared data, validation
- `scripts/hft_*.py` — CLI entrypoints
- `apps/workbench/src/artifacts/paths.py` — artifact path helpers

## Non-negotiable rule

Scenarios may share immutable inputs (`prepared_data_hash`, feature timelines) but never share mutable simulation state.
