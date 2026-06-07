# Autoresearch Pipeline Roadmap - 2026-06-07

## Goal

Build the autoresearch pipeline into a repeatable path from idea intake to candidate generation, cheap vectorbt filtering, evidence packaging, and later gated promotion into the existing research workbench. The first code slice starts adaptive threshold candidate search while preserving the current default grid behavior.

## Phased Plan

1. Candidate generation controls
   - Preserve default deterministic grid thresholds for existing callers.
   - Add an explicit random threshold search mode with bounded `num_samples` and `max_iterations`.
   - Keep candidate IDs derived from model and strategy params so dedupe semantics remain stable.

2. Cheap screening
   - Feed expanded candidates into vectorbt or the cheapest available backtest lane.
   - Store per-candidate screening metadata without writing into trusted production data paths.
   - Reject candidates with insufficient trades, poor net PnL, or missing evidence.

3. Evidence packaging
   - Emit compact research cards with hypothesis, thresholds, screening outcome, and reproducibility inputs.
   - Link cards back to idea IDs and model families.
   - Keep skipped or blocked validation explicit in the artifact.

4. Workbench integration
   - Promote only screened candidates into the 51-model workbench flow.
   - Keep PDF structural models separate from HYP feature families unless a parity review explicitly approves coupling.
   - Treat Python latency as informational; C++ distributions remain production truth.

5. Verification and promotion gates
   - Run reviewer, pytest or gate scripts, and graph post steps before claiming merge readiness.
   - Add C++ parity validation whenever FeatureIndex or hot-path feature slots change.
   - Keep quarantined lanes isolated from production CME Databento NPZ paths.

## Constraints

- Graph commands were explicitly waived by the user for this slice; no graph commands were run.
- Default `generate_candidates(...)` grid behavior must remain unchanged.
- Workstation code must not wire into Rithmic capture, order submission, or broker hot paths.
- Rithmic trial, options parity, and low-float equities data must remain quarantined.
- Secrets stay out of git and generated artifacts.

## Current Status

- Planning document added.
- First coding slice added opt-in random threshold generation behind `search_mode="random"`.
- Merge-ready: no.
- Scope-green: no.
- Verify-run: `python -m pytest tests/test_research_pipeline.py::test_generate_candidates_respects_max -q` passed; direct random-mode smoke passed.
- Data-mode: offline research code path only.
- Known-gaps: graph waived by user; subagent reviewer waived by user; full scope gate not run.
