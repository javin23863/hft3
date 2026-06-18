# HftBacktest Campaign Architecture

Follow-on workstream separate from VectorBT batch screening. VectorBT produces immutable `screening_artifact.json` with `replay_eligibility_status`. This campaign runner consumes that artifact and runs independent full-fidelity HftBacktest scenarios.

## Ontology authority (VaultGate)

Canonical scope is **not** this file alone. Ground in Obsidian vault (`wiki/hot.md`, `decisions/2026-06-17 Feature-complete research authority correction`) plus repo:

- [OPPORTUNITY_RESEARCH_SPEC.md](OPPORTUNITY_RESEARCH_SPEC.md) (when present)
- [VECTORBT_SCREENING_ENGINE_SPEC.md](VECTORBT_SCREENING_ENGINE_SPEC.md) (when present)
- [FEATURE_LITERATURE_TRACEABILITY_MATRIX.md](FEATURE_LITERATURE_TRACEABILITY_MATRIX.md)
- [MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md](../cockpit/MACRO_CONTEXT_VIX_OPTIONS_CHECKLIST.md)

Every scenario declares `feature_plane_status` (`feature_complete_pit_declared` | `scheduled_event_only` | `bar_stub_research_only` | `incomplete_feature_plane`). Default campaign path: `scheduled_event_only` until full PIT feature plane is proven.

Execution-realism claims require official HftBacktest **and** hft3 native C++ hot-path evidence (vault `wiki/hot.md` 2026-06-16). Broad paid compute requires VectorBT Rust engine or fail-closed.

## Layers

1. **Stage 0 validation** — screening hash, eligibility, prepared data, latency/queue/fee, source lock
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
