from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from backtest_pipeline.src.vectorbt_adapter import (
    compute_screening_artifact_hash,
    validate_screening_artifact,
)
from test_apply_robustness_evidence_to_screening import (
    _screening_artifact,
    _surface_pass,
    _write_json,
)
from test_robustness_bridge import _full_passing_input

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_SCRIPT = ROOT / "scripts" / "package_robustness_evidence_inputs.py"
APPLY_SCRIPT = ROOT / "scripts" / "apply_robustness_evidence_to_screening.py"


def _robustness_input() -> dict[str, Any]:
    robustness_input = copy.deepcopy(_full_passing_input())
    robustness_input["cscv_matrix"] = robustness_input["cscv_matrix"].tolist()
    return robustness_input


def _source_file(tmp_path: Path, rel_path: str = "sources/wfc_rows.json") -> tuple[str, str]:
    path = tmp_path / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"source":"test"}\n', encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return rel_path, digest


def _raw_inputs(
    *,
    candidate_id: str,
    source_path: str,
    screening_artifact_hash: str,
    include_input: bool = True,
    schema: str | None = "hft3_robustness_raw_inputs_v1",
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "source_evidence": {
            "wfc_rows": {
                "path": source_path,
            },
        },
        "surface_stability_metrics": _surface_pass(),
        "robustness_gate_scope": "operator_explicit_robustness_evidence",
    }
    if include_input:
        entry["robustness_input"] = _robustness_input()
    payload = {
        "screening_artifact_hash": screening_artifact_hash,
        "candidates": {
            candidate_id: entry,
        },
    }
    if schema is not None:
        payload["schema"] = schema
    return payload


def test_packages_raw_inputs_and_apply_makes_candidate_eligible(tmp_path: Path) -> None:
    candidate_id = "cand_apply"
    screening_path = tmp_path / "screening_artifact.json"
    raw_path = tmp_path / "raw_robustness_inputs.json"
    evidence_path = tmp_path / "robustness_evidence.json"
    applied_path = tmp_path / "screening_artifact.robust.json"
    source_rel, source_hash = _source_file(tmp_path)
    artifact = _screening_artifact(candidate_id)
    _write_json(screening_path, artifact)
    _write_json(
        raw_path,
        _raw_inputs(
            candidate_id=candidate_id,
            source_path=source_rel,
            screening_artifact_hash=artifact["screening_artifact_hash"],
        ),
    )

    packaged = subprocess.run(
        [
            sys.executable,
            str(PACKAGE_SCRIPT),
            "--screening-artifact",
            str(screening_path),
            "--robustness-inputs",
            str(raw_path),
            "--source-root",
            str(tmp_path),
            "--out",
            str(evidence_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert packaged.returncode == 0, packaged.stderr
    receipt = json.loads(packaged.stdout)
    assert receipt["status"] == "ok"
    assert receipt["packaged_candidate_ids"] == [candidate_id]
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    entry = payload["candidates"][candidate_id]
    assert payload["schema"] == "hft3_robustness_evidence_inputs_v1"
    assert entry["binding"]["screening_artifact_hash"] == artifact["screening_artifact_hash"]
    assert entry["binding"]["parameter_values_hash"] == artifact["promoted"][0]["parameter_values_hash"]
    assert entry["binding"]["feature_recipe_hash"] == artifact["promoted"][0]["feature_recipe_hash"]
    assert entry["source_evidence"]["wfc_rows"]["sha256"] == source_hash

    applied = subprocess.run(
        [
            sys.executable,
            str(APPLY_SCRIPT),
            "--screening-artifact",
            str(screening_path),
            "--robustness-evidence",
            str(evidence_path),
            "--out",
            str(applied_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert applied.returncode == 0, applied.stderr
    updated = json.loads(applied_path.read_text(encoding="utf-8"))
    validate_screening_artifact(updated)
    row = updated["promoted"][0]
    assert row["replay_eligibility_status"] == "eligible"
    assert row["robustness_evidence_receipt"]["binding"]["candidate_id"] == candidate_id


def test_embedded_row_inputs_without_raw_file_fail_closed(tmp_path: Path) -> None:
    candidate_id = "cand_apply"
    screening_path = tmp_path / "screening_artifact.json"
    evidence_path = tmp_path / "robustness_evidence.json"
    source_rel, _source_hash = _source_file(tmp_path)
    artifact = _screening_artifact(candidate_id)
    row = artifact["promoted"][0]
    row["vectorbt_results"] = {"robustness_input": _robustness_input()}
    row["surface_stability_metrics"] = _surface_pass()
    row["source_evidence"] = {"wfc_rows": {"path": source_rel}}
    artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)
    validate_screening_artifact(artifact)
    _write_json(screening_path, artifact)

    result = subprocess.run(
        [
            sys.executable,
            str(PACKAGE_SCRIPT),
            "--screening-artifact",
            str(screening_path),
            "--source-root",
            str(tmp_path),
            "--out",
            str(evidence_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "the following arguments are required: --robustness-inputs" in result.stderr
    assert not evidence_path.exists()


def test_missing_robustness_input_fails_without_output(tmp_path: Path) -> None:
    candidate_id = "cand_apply"
    screening_path = tmp_path / "screening_artifact.json"
    raw_path = tmp_path / "raw_robustness_inputs.json"
    evidence_path = tmp_path / "should_not_exist.json"
    source_rel, _source_hash = _source_file(tmp_path)
    artifact = _screening_artifact(candidate_id)
    _write_json(screening_path, artifact)
    _write_json(
        raw_path,
        _raw_inputs(
            candidate_id=candidate_id,
            source_path=source_rel,
            screening_artifact_hash=artifact["screening_artifact_hash"],
            include_input=False,
        ),
    )

    result = subprocess.run(
        [
            sys.executable,
            str(PACKAGE_SCRIPT),
            "--screening-artifact",
            str(screening_path),
            "--robustness-inputs",
            str(raw_path),
            "--source-root",
            str(tmp_path),
            "--out",
            str(evidence_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "packaged_count_below_min" in result.stderr
    assert "robustness_input_missing" in result.stderr
    assert not evidence_path.exists()


def test_missing_source_hash_fails_without_output(tmp_path: Path) -> None:
    candidate_id = "cand_apply"
    screening_path = tmp_path / "screening_artifact.json"
    raw_path = tmp_path / "raw_robustness_inputs.json"
    evidence_path = tmp_path / "should_not_exist.json"
    artifact = _screening_artifact(candidate_id)
    _write_json(screening_path, artifact)
    _write_json(
        raw_path,
        _raw_inputs(
            candidate_id=candidate_id,
            source_path="missing/source.json",
            screening_artifact_hash=artifact["screening_artifact_hash"],
        ),
    )

    result = subprocess.run(
        [
            sys.executable,
            str(PACKAGE_SCRIPT),
            "--screening-artifact",
            str(screening_path),
            "--robustness-inputs",
            str(raw_path),
            "--source-root",
            str(tmp_path),
            "--out",
            str(evidence_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "packaged_count_below_min" in result.stderr
    assert "source_evidence_hash_missing:wfc_rows" in result.stderr
    assert not evidence_path.exists()


def test_raw_schema_is_required_without_output(tmp_path: Path) -> None:
    candidate_id = "cand_apply"
    screening_path = tmp_path / "screening_artifact.json"
    raw_path = tmp_path / "raw_robustness_inputs.json"
    evidence_path = tmp_path / "should_not_exist.json"
    source_rel, _source_hash = _source_file(tmp_path)
    artifact = _screening_artifact(candidate_id)
    _write_json(screening_path, artifact)
    _write_json(
        raw_path,
        _raw_inputs(
            candidate_id=candidate_id,
            source_path=source_rel,
            screening_artifact_hash=artifact["screening_artifact_hash"],
            schema=None,
        ),
    )

    result = subprocess.run(
        [
            sys.executable,
            str(PACKAGE_SCRIPT),
            "--screening-artifact",
            str(screening_path),
            "--robustness-inputs",
            str(raw_path),
            "--source-root",
            str(tmp_path),
            "--out",
            str(evidence_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "unsupported_robustness_raw_schema" in result.stderr
    assert not evidence_path.exists()


def test_raw_screening_artifact_hash_mismatch_fails_without_output(tmp_path: Path) -> None:
    candidate_id = "cand_apply"
    screening_path = tmp_path / "screening_artifact.json"
    raw_path = tmp_path / "raw_robustness_inputs.json"
    evidence_path = tmp_path / "should_not_exist.json"
    source_rel, _source_hash = _source_file(tmp_path)
    artifact = _screening_artifact(candidate_id)
    _write_json(screening_path, artifact)
    _write_json(
        raw_path,
        _raw_inputs(
            candidate_id=candidate_id,
            source_path=source_rel,
            screening_artifact_hash="sha256:wrong_artifact",
        ),
    )

    result = subprocess.run(
        [
            sys.executable,
            str(PACKAGE_SCRIPT),
            "--screening-artifact",
            str(screening_path),
            "--robustness-inputs",
            str(raw_path),
            "--source-root",
            str(tmp_path),
            "--out",
            str(evidence_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "robustness_inputs_screening_artifact_hash_mismatch" in result.stderr
    assert not evidence_path.exists()
