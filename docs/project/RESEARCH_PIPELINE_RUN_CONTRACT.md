# Research Pipeline Run Contract

Status: active contract for the one-input/one-output hft3 research pipeline.

Authority: `docs/vault/UNIFIED_RESEARCH_PIPELINE.md`,
`docs/vault/RESEARCH_ENTRYPOINTS.md`, `docs/project/AUTORESEARCH_PIPELINE_UPGRADE_PLAN.md`.

## Goal

The pipeline is not a loose checklist of artifacts. A run starts from one
`ResearchRunSpec` JSON file and ends in one `ResearchRunBundle` directory. Each
stage writes a receipt. A downstream stage can run only after the upstream
receipt proves the output is usable, not merely present.

```text
ResearchRunSpec
  -> preflight
  -> VectorBT screen
  -> promoted aggregation
  -> robustness evidence/training
  -> HftBacktest realism
  -> workbench update
  -> model library enrollment
  -> ResearchRunBundle
```

The current entrypoint is:

```bash
python scripts/run_research_pipeline.py --spec runtime/reports/<run_spec>.json --resume
```

## Bundle Layout

```text
research_cards/pipeline_runs/<run_id>/
  run_bundle.json
  status.json
  00_stage_0_ontology/
  01_stage_1_vectorbt_screen/
  02_stage_2_promoted_aggregation/
  03_stage_2_robustness_evidence/
  04_stage_3_hftbacktest_realism/
  05_stage_4_workbench_robustness/
  06_stage_5_lifecycle_behavior_tracking/
  receipts/
```

`status.json` is the operator-facing file. `run_bundle.json` is the durable
handoff index.

## Minimum Spec Shape

```json
{
  "version": 1,
  "run_id": "paid_recovery_YYYYMMDDTHHMMSSZ",
  "repo_root": "C:/Users/MSI/repos/hft3",
  "bundle_root": "research_cards/pipeline_runs",
  "target_stage": "stage_5_lifecycle_behavior_tracking",
  "preflight": {
    "required_paths": {
      "events_csv": "data_system/config/events.csv",
      "feature_store_root": "data/features",
      "npz_root": "C:/hft3-lake/npz"
    },
    "required_env": ["HFT3_NPZ_ROOT"]
  },
  "stages": {
    "stage_1_vectorbt_screen": {
      "command": ["bash", "scripts/run_vbt_paid_screen_vast_full.sh"],
      "outputs": {
        "paid_run_manifest": "research_cards/pipeline_runs/<vbt_run>/paid_screen_run_manifest.json"
      }
    },
    "stage_2_promoted_aggregation": {
      "command": [
        "python",
        "scripts/aggregate_vbt_promoted_ids.py",
        "--manifest",
        "research_cards/pipeline_runs/<vbt_run>/paid_screen_run_manifest.json",
        "--out",
        "research_cards/pipeline_runs/<run_id>/02_stage_2_promoted_aggregation/promoted_candidates.json"
      ],
      "outputs": {
        "promoted_candidates": "research_cards/pipeline_runs/<run_id>/02_stage_2_promoted_aggregation/promoted_candidates.json"
      }
    },
    "stage_2_robustness_evidence": {
      "commands": [
        [
          "python",
          "scripts/build_robustness_raw_inputs_from_screening.py",
          "--screening-artifact",
          "research_cards/pipeline_runs/<vbt_run>/screening_artifact.json",
          "--out",
          "research_cards/pipeline_runs/<run_id>/03_stage_2_robustness_evidence/raw_inputs.json"
        ],
        [
          "python",
          "scripts/package_robustness_evidence_inputs.py",
          "--raw-inputs",
          "research_cards/pipeline_runs/<run_id>/03_stage_2_robustness_evidence/raw_inputs.json",
          "--out",
          "research_cards/pipeline_runs/<run_id>/03_stage_2_robustness_evidence/evidence_inputs.json"
        ],
        [
          "python",
          "scripts/apply_robustness_evidence_to_screening.py",
          "--screening-artifact",
          "research_cards/pipeline_runs/<vbt_run>/screening_artifact.json",
          "--robustness-evidence-inputs",
          "research_cards/pipeline_runs/<run_id>/03_stage_2_robustness_evidence/evidence_inputs.json",
          "--out",
          "research_cards/pipeline_runs/<run_id>/03_stage_2_robustness_evidence/applied_screening_artifact.json",
          "--min-eligible",
          "1"
        ]
      ],
      "outputs": {
        "robustness_evidence_receipt": "research_cards/pipeline_runs/<run_id>/03_stage_2_robustness_evidence/evidence_inputs.json",
        "applied_screening_artifact": "research_cards/pipeline_runs/<run_id>/03_stage_2_robustness_evidence/applied_screening_artifact.json"
      }
    }
  }
}
```

Later stages use the same structure: command plus declared outputs. The runner
does not guess hidden output paths.

## Fail-Closed Rules

The runner blocks the run when:

- a preflight path or required environment variable is missing;
- VectorBT outputs contain zero promoted ids;
- VectorBT outputs contain zero positive trade rows;
- VectorBT outputs use `bar_stub_research_only`;
- VectorBT outputs use `fs_v1_pilot_unknown`;
- VectorBT outputs use `ohlcv_1m_from_npz_or_supplied_array`;
- promoted aggregation emits zero promoted candidates;
- robustness evidence emits zero `replay_eligibility_status=eligible` rows;
- downstream JSON outputs declare failed, blocked, error, aborted, or stalled status.

This specifically prevents the `paid_full_20260626T054456Z` failure mode: a full
run may finish, but it cannot advance unless it produced real promotable
evidence.

## Resume Contract

`--resume` reads `receipts/<stage_id>.json`. Stages with `status="passed"` are
not rerun. The next stage begins from the first missing or non-passed receipt.

This is the only allowed recovery behavior for a partially completed pipeline:
continue from the last valid receipt, never restart the whole run by default.

## Current Boundary

`scripts/run_research_pipeline.py` is an orchestration contract, not a new
screening engine. It calls existing stage scripts and validates their declared
outputs. The VectorBT, robustness, HftBacktest, workbench, and lifecycle engines
remain the source of truth for their own calculations.
