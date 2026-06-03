"""Artifact bundle validation (Phase 12).

The Phase 12 spec requires every experiment to write a structured
artifact bundle under `artifacts/runs/{run_id}/` with 19 required
files. This module provides:

  - `REQUIRED_ARTIFACTS` constant (the 19 files)
  - `OPTIONAL_ARTIFACTS` constant (files that may be present)
  - `ArtifactBundleResult` dataclass with validation results
  - `validate_bundle(bundle_dir)` function that checks all required
    files are present and returns a structured result

The runner's `stage_artifact_bundle` calls `validate_bundle()` before
writing the manifest. If validation fails, the runner emits a
BLOCKING gate with reason code `ARTIFACT_BUNDLE_INCOMPLETE`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# The 19 required files per the Phase 12 spec.
REQUIRED_ARTIFACTS: tuple[str, ...] = (
    "manifest.json",
    "config_snapshot.yaml",
    "config_hash.txt",
    "git_commit.txt",
    "data_lineage.json",
    "feature_lineage.json",
    "research_input_reference.json",
    "experiment_spec.json",
    "model_combination.json",
    "execution_assumptions.json",
    "backtest_metrics.json",
    "robustness_gates.json",
    "walk_forward_results.json",
    "walk_forward_correlation.json",
    "scoring_summary.json",
    "promotion_decision.json",
    "registry_update.json",
    "report.md",
    "logs.txt",
)

# Optional files that may be present (not required by the spec).
OPTIONAL_ARTIFACTS: tuple[str, ...] = (
    "data_resolution.json",  # Phase 6
    "config_hash.txt",       # already in REQUIRED, but listed here for clarity
)


@dataclass
class ArtifactBundleResult:
    """Result of validating an artifact bundle."""

    bundle_dir: str = ""
    required_present: List[str] = field(default_factory=list)
    required_missing: List[str] = field(default_factory=list)
    optional_present: List[str] = field(default_factory=list)
    pass_fail: bool = False
    reason_code: str = ""

    def to_dict(self) -> dict:
        return {
            "bundle_dir": self.bundle_dir,
            "required_present": list(self.required_present),
            "required_missing": list(self.required_missing),
            "optional_present": list(self.optional_present),
            "pass_fail": self.pass_fail,
            "reason_code": self.reason_code,
        }


def validate_bundle(bundle_dir: Path) -> ArtifactBundleResult:
    """Validate that all 17 required artifacts are present in `bundle_dir`.

    Returns an `ArtifactBundleResult` with:
      - `required_present`: list of required files that exist
      - `required_missing`: list of required files that are missing
      - `optional_present`: list of optional files that exist
      - `pass_fail`: True iff all required files are present
      - `reason_code`: "ARTIFACT_BUNDLE_COMPLETE" or "ARTIFACT_BUNDLE_INCOMPLETE"
    """
    required_present: list[str] = []
    required_missing: list[str] = []
    for name in REQUIRED_ARTIFACTS:
        if (bundle_dir / name).is_file():
            required_present.append(name)
        else:
            required_missing.append(name)

    optional_present: list[str] = []
    for name in OPTIONAL_ARTIFACTS:
        if (bundle_dir / name).is_file() and name not in REQUIRED_ARTIFACTS:
            optional_present.append(name)

    passed = len(required_missing) == 0
    reason_code = (
        "ARTIFACT_BUNDLE_COMPLETE" if passed else "ARTIFACT_BUNDLE_INCOMPLETE"
    )

    return ArtifactBundleResult(
        bundle_dir=str(bundle_dir),
        required_present=required_present,
        required_missing=required_missing,
        optional_present=optional_present,
        pass_fail=passed,
        reason_code=reason_code,
    )


def to_gate_result(result: ArtifactBundleResult) -> "GateResult":  # type: ignore[name-defined]
    """Convert an ArtifactBundleResult to a Phase 8 GateResult.

    The gate is BLOCKING iff the bundle is incomplete (missing required files).
    """
    from hft3.validation.gate_result import (
        GateCategory, GateResult, Severity,
    )
    blocking = not result.pass_fail
    severity = Severity.BLOCKING if blocking else Severity.INFO
    return GateResult(
        gate_name="artifact_bundle_completeness",
        gate_category=GateCategory.ARTIFACT_COMPLETENESS,
        metric_name="required_files_present",
        threshold=len(REQUIRED_ARTIFACTS),
        observed_value=len(result.required_present),
        comparison_operator="==",
        pass_fail=result.pass_fail,
        severity=severity,
        blocking_status=blocking,
        reason_code=result.reason_code,
        artifact_reference="manifest.json",
    )
