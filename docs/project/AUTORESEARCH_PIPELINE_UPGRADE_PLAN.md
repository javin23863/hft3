# Autoresearch Pipeline Upgrade Plan

Status: implementation plan for the latest pipeline-upgrade PR.

Authority: `docs/research/AUTORESEARCH_PIPELINE.md`, `docs/project/VBT_PAID_SCREEN_RUNBOOK.md`, `docs/project/ROBUSTNESS_PIPELINE_SOURCE_OF_TRUTH.md`, vault `wiki/hot.md`, and `operations/2026-06-23 Pre-VastAI smoke handoff.md`.

## Goal

Improve the legacy `scripts/run_pipeline.py` autoresearch entrypoint without recreating the already back-tested VectorBT/HftBacktest pipeline.

The upgrade is intentionally narrow:

- make runs reproducible through a central runtime config and persisted config hash
- make failures auditable through structured run logs and receipts
- cache repeated document ingestion work
- expose a callable pipeline surface through `main(argv=...)` and helper functions
- add a lightweight candidate prefilter before expensive evaluation
- preserve paid-screen v2 as the canonical fast VectorBT workhorse

## Non-Negotiable Boundaries

- Do not change the canonical engine order: VectorBT / Vector VT screen, then robustness evidence, then strict HftBacktest/HFB replay.
- Do not launch HftBacktest or VastAI from a VectorBT artifact until `apply_robustness_evidence_to_screening.py --min-eligible 1` passes.
- Do not duplicate `scripts/run_vectorbt_paid_screen_v2.py`; that lane already owns long-lived workers, batching, cache keys, resume, and high-worker execution.
- Do not run Python workers on the MSI workstation. Verification and performance measurement run on CHI404 or Vast.
- Do not relax Rust requirements for paid/broad VectorBT scopes.

## Upgrade Work Items

| Area | Change | Success Receipt |
|---|---|---|
| Central config | Add a JSON runtime config for legacy `run_pipeline.py` defaults. CLI flags still override config. | `pipeline_runtime_config.json` in each run directory with a deterministic config hash. |
| Structured logging | Add per-run file logging and structured run receipt emission. | `pipeline_run.log` and `pipeline_run_receipt.json` exist for every artifact-producing run. |
| Document cache | Cache extraction, summary, and KG slice records by source fingerprint. | Second run against same document reports a cache hit without re-extracting the document. |
| Candidate prefilter | Reject malformed or obviously invalid candidates before VectorBT or legacy evaluation. | `candidate_prefilter.json` records input, kept, rejected, and reasons. |
| Programmatic call | Allow `main(argv=...)` and keep orchestration helpers importable for tests/services. | Unit tests call helpers without shelling out. |
| Measurement | Compare before/after on CHI404 with the same small VectorBT smoke shape. | Receipts record runtime, cache status, candidate counts, and VectorBT artifact path/hash. |

## Quality And Speed Mapping

Central config improves quality by making the run shape auditable and replayable. It improves speed indirectly by carrying known-good VectorBT budget and cache defaults instead of rediscovering them per command.

Structured logging improves quality by making failures inspectable after the fact. It also reduces restart time because operators can inspect a run receipt instead of scraping mixed stdout/stderr.

Document caching improves quality by binding a document fingerprint to the extracted summary and KG slice. It improves speed by avoiding repeated PDF/DOCX/URL extraction and repeated summarization on identical inputs.

Candidate prefiltering improves quality by keeping malformed parameter sets out of downstream artifacts. It improves speed by dropping obviously invalid candidates before VectorBT or legacy workbench evaluation.

Parallel and compiled execution are already owned by paid-screen v2, VectorBT Rust, HftBacktest, and native C++ hot-path lanes. This PR should document and preserve those lanes rather than adding a second concurrent evaluator inside `run_pipeline.py`.

## Measurement Plan

Run only on CHI404 for verification/performance:

```bash
cd /tmp/<clean-merged-main-or-pr-checkout>
PY=/tmp/hft3-pr13-greptile-venv/bin/python

$PY -m py_compile scripts/run_pipeline.py tests/test_research_pipeline.py
$PY -m pytest -q tests/test_research_pipeline.py

$PY scripts/run_pipeline.py \
  --thesis "Fade spread blowout after CPI" \
  --event-id CPI_2024_09_11_TIGHT \
  --dry-run \
  --no-llm \
  --max-candidates 3
```

After code verification, run a small CHI404 VectorBT smoke using the existing readiness-gated paid-screen path. Compare:

- wall-clock duration
- `units_per_hour`
- cache hit/miss counters where available
- candidate prefilter kept/rejected counts
- `pipeline_run_receipt.json` status
- `screening_artifact_hash`

Only after the same pipeline returns to the current robustness gate should the next step continue: build raw robustness inputs, package evidence, apply evidence, require at least one strict replay-eligible row, then run HftBacktest smoke.

## Review Gates

Required before claiming this PR is ready:

- local preflight `rg` loop for stale bypass language and missing receipt terms
- reviewer pass on the diff
- CHI404 py_compile and focused pytest
- plan drift review against this document
- review surface gate and PR AI review loop
- graph gate remains `waived-by-owner-2026-06-16`
