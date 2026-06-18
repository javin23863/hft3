from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from backtest_pipeline.src import hftbacktest_realism as hbt0
from backtest_pipeline.src.hftbacktest_realism import (
    HftBacktestRealismArtifactError,
    SOURCE_LOCK_REQUIRED_FIELDS,
    build_hftbacktest_source_lock,
    compute_hftbacktest_source_lock_hash,
    validate_hftbacktest_source_lock,
    validate_replay_summary,
    write_hbt0_artifacts,
)
from backtest_pipeline.src.vectorbt_adapter import compute_screening_artifact_hash

from hft_screening_fixtures import (
    NATIVE_CPP_LATENCY_EVIDENCE,
    screening_artifact_shell,
)

# Per Codex review finding 13: skip the entire hbt0 suite when the hftbacktest
# package is not importable.  hbt0 exercises the hftbacktest realism/source-lock
# contract; while several tests monkeypatch detect_hftbacktest_installation to
# test the fail-closed path, the suite as a whole is only meaningful when the
# real hftbacktest package is present in the environment.
_HFTBACKTEST_IMPORTABLE = importlib.util.find_spec("hftbacktest") is not None
pytestmark = pytest.mark.skipif(
    not _HFTBACKTEST_IMPORTABLE,
    reason="hftbacktest package not installed; skipping hbt0 realism suite",
)


def _screening_artifact(candidate_id: str = "cand_hbt0") -> dict:
    return screening_artifact_shell("vbt_handoff", candidate_id)


def _write_screening(path: Path, artifact: dict | None = None) -> Path:
    payload = artifact or _screening_artifact()
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def test_source_lock_schema_hash_and_required_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hbt0, "_repo_commit", lambda _root: "hft3sha")
    monkeypatch.setattr(hbt0, "_repo_dirty", lambda _root: False)

    lock = build_hftbacktest_source_lock(
        repo_root=tmp_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        native_hot_path_status="provided",
        created_at_utc="2026-06-16T00:00:00+00:00",
    )

    for field in SOURCE_LOCK_REQUIRED_FIELDS:
        assert field in lock
    assert lock["upstream_repo_url"] == hbt0.UPSTREAM_REPO_URL
    assert lock["upstream_commit_sha_or_tag"] == "v2.4.2"
    assert lock["native_hot_path_required"] is True
    assert lock["native_hot_path_status"] == "provided"
    assert "asset.constant_order_latency" in lock["api_surface_used"]
    assert all("_or_" not in item for item in lock["api_surface_used"])
    assert lock["hft3_commit"] == "hft3sha"
    assert lock["hft3_worktree_dirty"] is False
    assert lock["source_lock_hash"] == compute_hftbacktest_source_lock_hash(lock)
    assert validate_hftbacktest_source_lock(lock) == []


def test_source_lock_fails_closed_without_upstream_or_native_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hbt0, "_repo_commit", lambda _root: "hft3sha")

    lock = build_hftbacktest_source_lock(repo_root=tmp_path)
    reasons = validate_hftbacktest_source_lock(lock)

    assert "missing_source_lock_field:upstream_commit_sha_or_tag" in reasons
    assert "native_cpp_hot_path_evidence_missing" in reasons


def test_source_lock_fails_closed_on_empty_required_lists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hbt0, "_repo_commit", lambda _root: "hft3sha")
    lock = build_hftbacktest_source_lock(
        repo_root=tmp_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=["rithmic_gateway/tools/rithmic_latency_probe"],
        native_hot_path_status="provided",
    )
    lock["docs_pages_used"] = []
    lock["hft3_adapter_files"] = []
    lock["api_surface_used"] = []
    lock["source_lock_hash"] = compute_hftbacktest_source_lock_hash(lock)

    reasons = validate_hftbacktest_source_lock(lock)

    assert "empty_source_lock_field:docs_pages_used" in reasons
    assert "empty_source_lock_field:hft3_adapter_files" in reasons
    assert "empty_source_lock_field:api_surface_used" in reasons


def test_source_lock_fails_closed_on_non_list_required_list_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hbt0, "_repo_commit", lambda _root: "hft3sha")
    lock = build_hftbacktest_source_lock(
        repo_root=tmp_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=["rithmic_gateway/tools/rithmic_latency_probe"],
        native_hot_path_status="provided",
    )
    lock["docs_pages_used"] = "https://hftbacktest.readthedocs.io"
    lock["hft3_adapter_files"] = "packages/backtest_pipeline/src/hftbacktest_realism.py"
    lock["api_surface_used"] = "HashMapMarketDepthBacktest"
    lock["source_lock_hash"] = compute_hftbacktest_source_lock_hash(lock)

    reasons = validate_hftbacktest_source_lock(lock)

    assert "source_lock_field_not_list:docs_pages_used" in reasons
    assert "source_lock_field_not_list:hft3_adapter_files" in reasons
    assert "source_lock_field_not_list:api_surface_used" in reasons


@pytest.mark.parametrize(
    "field",
    [
        "native_hot_path_evidence",
        "docs_pages_used",
        "hft3_adapter_files",
        "known_doc_repo_discrepancies",
    ],
)
def test_source_lock_builder_rejects_string_list_arguments(
    tmp_path: Path,
    field: str,
) -> None:
    kwargs = {
        "repo_root": tmp_path,
        "upstream_ref": "v2.4.2",
        "native_hot_path_evidence": [NATIVE_CPP_LATENCY_EVIDENCE],
    }
    kwargs[field] = "not-a-list"

    with pytest.raises(HftBacktestRealismArtifactError, match=f"{field} must be a list"):
        build_hftbacktest_source_lock(**kwargs)


def test_source_lock_status_provided_still_requires_native_evidence(
    tmp_path: Path,
) -> None:
    lock = build_hftbacktest_source_lock(
        repo_root=tmp_path,
        upstream_ref="v2.4.2",
        native_hot_path_status="provided",
    )

    reasons = validate_hftbacktest_source_lock(lock)

    assert "native_cpp_hot_path_evidence_missing" in reasons


def test_source_lock_rejects_unrecognized_native_hot_path_evidence(
    tmp_path: Path,
) -> None:
    lock = build_hftbacktest_source_lock(
        repo_root=tmp_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=["risk_engine_fake_claim.json"],
        native_hot_path_status="provided",
    )

    reasons = validate_hftbacktest_source_lock(lock)

    assert "native_cpp_hot_path_evidence_unrecognized" in reasons


def test_unavailable_hftbacktest_package_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        hbt0,
        "detect_hftbacktest_installation",
        lambda: {
            "available": False,
            "python_package_name": "hftbacktest",
            "python_package_version": "unavailable",
            "installed_module_path": "unavailable",
        },
    )

    lock = build_hftbacktest_source_lock(
        repo_root=tmp_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        native_hot_path_status="provided",
    )
    reasons = validate_hftbacktest_source_lock(lock)

    assert "hftbacktest_unavailable" in reasons
    assert "hftbacktest_module_path_unavailable" in reasons


def test_source_lock_unverified_upstream_ref_is_not_self_reported_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        hbt0,
        "detect_hftbacktest_installation",
        lambda: {
            "available": True,
            "python_package_name": "hftbacktest",
            "python_package_version": "2.4.2",
            "installed_module_path": "site-packages/hftbacktest/__init__.py",
        },
    )

    lock = build_hftbacktest_source_lock(
        repo_root=tmp_path,
        upstream_ref="not-a-real-hbt-ref",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        native_hot_path_status="provided",
    )
    reasons = validate_hftbacktest_source_lock(lock)

    assert lock["python_package_version"] == "2.4.2"
    assert lock["upstream_commit_sha_or_tag"] == "not-a-real-hbt-ref"
    assert lock["upstream_ref_verification_status"] == "unverified_ref_package_version_mismatch"
    assert "source_lock_upstream_ref_unverified" in reasons


def test_source_lock_verified_upstream_ref_matches_installed_hftbacktest_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        hbt0,
        "detect_hftbacktest_installation",
        lambda: {
            "available": True,
            "python_package_name": "hftbacktest",
            "python_package_version": "2.4.2",
            "installed_module_path": "site-packages/hftbacktest/__init__.py",
        },
    )

    lock = build_hftbacktest_source_lock(
        repo_root=tmp_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        native_hot_path_status="provided",
    )

    assert lock["upstream_ref_verification_status"] == "package_version_match"
    assert validate_hftbacktest_source_lock(lock) == []


def test_replay_pass_without_source_lock_is_refused() -> None:
    summary = {
        field: "x"
        for field in hbt0.REPLAY_SUMMARY_REQUIRED_FIELDS
    }
    summary.update(
        {
            "replay_realism_status": "pass",
            "accelerated_mode": False,
            "fail_closed_reasons": [],
        }
    )

    reasons = validate_replay_summary(summary, source_lock=None)

    assert "hbt0_pass_status_forbidden" in reasons
    assert "pass_artifact_missing_source_lock" in reasons


@pytest.mark.parametrize(
    "accelerated_mode",
    [
        True,
        pytest.param(None, id="missing"),
    ],
)
def test_replay_summary_accelerated_mode_fails_closed(accelerated_mode: bool | None) -> None:
    summary = {
        field: "x"
        for field in hbt0.REPLAY_SUMMARY_REQUIRED_FIELDS
    }
    summary.update(
        {
            "replay_realism_status": "research_only",
            "accelerated_mode": accelerated_mode,
            "fail_closed_reasons": [],
        }
    )
    if accelerated_mode is None:
        summary.pop("accelerated_mode")

    reasons = validate_replay_summary(summary, source_lock=None)

    assert "accelerated_mode_cannot_certify_hbt0" in reasons
    if accelerated_mode is None:
        assert "missing_replay_summary_field:accelerated_mode" in reasons


def test_accelerated_fail_reason_maps_to_non_certifying_status() -> None:
    status = hbt0._replay_status_from_fail_reasons(  # noqa: SLF001 - regression for HBT status contract.
        ["accelerated_mode_cannot_certify_hbt0"],
        {"data_validation_status": "pass"},
    )

    assert status == "accelerated_not_certifying"


@pytest.mark.parametrize(
    "field",
    [
        "accuracy_tradeoff_declared",
        "queue_position_modeled",
        "order_response_latency_modeled",
        "full_replay_comparison_hash_or_not_run",
        "certification_allowed",
    ],
)
def test_replay_summary_missing_accelerated_metadata_fails_closed(field: str) -> None:
    summary = {
        field_name: "x"
        for field_name in hbt0.REPLAY_SUMMARY_REQUIRED_FIELDS
    }
    summary.update(
        {
            "replay_realism_status": "research_only",
            "accelerated_mode": False,
            "accuracy_tradeoff_declared": False,
            "queue_position_modeled": False,
            "order_response_latency_modeled": False,
            "full_replay_comparison_hash_or_not_run": "not_run",
            "certification_allowed": False,
            "fail_closed_reasons": [],
        }
    )
    summary.pop(field)

    reasons = validate_replay_summary(summary, source_lock=None)

    assert f"missing_replay_summary_field:{field}" in reasons


def test_replay_summary_certification_allowed_requires_non_accelerated_official_replay() -> None:
    summary = {
        field: "x"
        for field in hbt0.REPLAY_SUMMARY_REQUIRED_FIELDS
    }
    summary.update(
        {
            "replay_realism_status": "research_only",
            "accelerated_mode": True,
            "accuracy_tradeoff_declared": True,
            "queue_position_modeled": False,
            "order_response_latency_modeled": False,
            "full_replay_comparison_hash_or_not_run": "not_run",
            "certification_allowed": True,
            "official_hftbacktest_replay_status": "not_run",
            "fail_closed_reasons": [],
        }
    )

    reasons = validate_replay_summary(summary, source_lock=None)

    assert "accelerated_mode_cannot_certify_hbt0" in reasons
    assert "certification_allowed_requires_non_accelerated_mode" in reasons
    assert "certification_allowed_requires_official_hftbacktest_replay" in reasons
    assert "certification_allowed_requires_full_replay_comparison_hash" in reasons


def test_replay_pass_is_forbidden_even_with_valid_source_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hbt0, "_repo_commit", lambda _root: "hft3sha")
    lock = build_hftbacktest_source_lock(
        repo_root=tmp_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        native_hot_path_status="provided",
    )
    summary = {
        field: "x"
        for field in hbt0.REPLAY_SUMMARY_REQUIRED_FIELDS
    }
    summary.update(
        {
            "hftbacktest_source_lock_hash": lock["source_lock_hash"],
            "replay_realism_status": "pass",
            "accelerated_mode": False,
            "fail_closed_reasons": [],
        }
    )

    reasons = validate_replay_summary(summary, source_lock=lock)

    assert "hbt0_pass_status_forbidden" in reasons


def test_replay_pass_with_fail_closed_reasons_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hbt0, "_repo_commit", lambda _root: "hft3sha")
    lock = build_hftbacktest_source_lock(
        repo_root=tmp_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        native_hot_path_status="provided",
    )
    summary = {
        field: "x"
        for field in hbt0.REPLAY_SUMMARY_REQUIRED_FIELDS
    }
    summary.update(
        {
            "hftbacktest_source_lock_hash": lock["source_lock_hash"],
            "replay_realism_status": "pass",
            "accelerated_mode": False,
            "fail_closed_reasons": ["data_validation_not_run"],
        }
    )

    reasons = validate_replay_summary(summary, source_lock=lock)

    assert "pass_artifact_has_fail_closed_reasons" in reasons


def test_hbt0_writes_source_lock_and_fail_closed_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hbt0, "_repo_commit", lambda _root: "hft3sha")
    screening_path = _write_screening(tmp_path / "screening_artifact.json")

    payload = write_hbt0_artifacts(
        repo_root=tmp_path,
        out_dir=tmp_path / "research_cards" / "hftbacktest_realism" / "hbt0_test",
        screening_artifact_path=screening_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        run_id="hbt0_test",
    )

    source_lock_path = Path(payload["source_lock_path"])
    summary_path = Path(payload["replay_summary_path"])
    assert source_lock_path.name == "hftbacktest_source_lock.json"
    assert source_lock_path.is_file()
    assert summary_path.is_file()

    lock = json.loads(source_lock_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["hftbacktest_source_lock_hash"] == lock["source_lock_hash"]
    assert summary["screening_artifact_hash"] == _screening_artifact()["screening_artifact_hash"]
    assert summary["candidate_id"] == "cand_hbt0"
    assert summary["model_id"] == "HYP_5"
    assert summary["symbol"] == "MES"
    assert summary["replay_realism_status"] == "research_only"
    assert "hbt0_source_lock_only_replay_not_run" in summary["fail_closed_reasons"]


def test_hbt0_refuses_screening_artifact_hash_mismatch(tmp_path: Path) -> None:
    artifact = _screening_artifact()
    artifact["screening_artifact_hash"] = "bogus"
    screening_path = _write_screening(tmp_path / "screening_artifact.json", artifact)

    payload = write_hbt0_artifacts(
        repo_root=tmp_path,
        out_dir=tmp_path / "out",
        screening_artifact_path=screening_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        run_id="hbt0_hash_mismatch",
    )

    assert "screening_artifact_hash_mismatch" in payload["replay_summary"]["fail_closed_reasons"]


def test_hbt0_refuses_missing_terminal_screening_hash(tmp_path: Path) -> None:
    artifact = _screening_artifact()
    artifact.pop("screening_artifact_hash")
    screening_path = _write_screening(tmp_path / "screening_artifact.json", artifact)

    payload = write_hbt0_artifacts(
        repo_root=tmp_path,
        out_dir=tmp_path / "out",
        screening_artifact_path=screening_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        run_id="hbt0_missing_screening_hash",
    )

    reasons = payload["replay_summary"]["fail_closed_reasons"]
    assert "missing_screening_artifact_field:screening_artifact_hash" in reasons
    assert "screening_artifact_hash_missing" in reasons
    assert payload["replay_summary"]["screening_artifact_hash"] == ""


def test_hbt0_refuses_malformed_nonterminal_screening_artifact(tmp_path: Path) -> None:
    artifact = _screening_artifact()
    artifact.pop("no_lookahead_signal_shift_proof")
    artifact["candidate_reasons"] = []
    artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)
    screening_path = _write_screening(tmp_path / "screening_artifact.json", artifact)

    payload = write_hbt0_artifacts(
        repo_root=tmp_path,
        out_dir=tmp_path / "out",
        screening_artifact_path=screening_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        run_id="hbt0_malformed_screening",
    )

    reasons = payload["replay_summary"]["fail_closed_reasons"]
    assert "missing_screening_artifact_field:no_lookahead_signal_shift_proof" in reasons
    assert "screening_artifact_field_not_mapping:candidate_reasons" in reasons


def test_hbt0_refuses_required_non_rust_screening_artifact(tmp_path: Path) -> None:
    artifact = _screening_artifact()
    artifact["vectorbt_engine"] = "numba"
    artifact["rust_engine_available"] = False
    artifact["engine_parity_status"] = "rust_engine_required_unavailable_fail_closed"
    artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)
    screening_path = _write_screening(tmp_path / "screening_artifact.json", artifact)

    payload = write_hbt0_artifacts(
        repo_root=tmp_path,
        out_dir=tmp_path / "out",
        screening_artifact_path=screening_path,
        data_npz_path=tmp_path / "not_a_valid_npz.npz",
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        run_id="hbt0_non_rust_screening",
    )

    reasons = payload["replay_summary"]["fail_closed_reasons"]
    assert "screening_artifact_required_rust_engine_missing" in reasons
    assert "screening_artifact_required_rust_engine_unavailable" in reasons
    assert "DATA_NPZ_READ_FAILED" in reasons
    assert payload["replay_summary"]["replay_realism_status"] == "fail"


@pytest.mark.parametrize("screening_scope", ["screen", "broad", "broad-screen", "broad_screen", "all_model", "paid"])
def test_hbt0_derives_rust_requirement_from_broad_screening_scope(
    tmp_path: Path,
    screening_scope: str,
) -> None:
    artifact = _screening_artifact()
    artifact["screening_scope"] = screening_scope
    artifact["rust_engine_required_for_scope"] = False
    artifact["vectorbt_engine"] = "numba"
    artifact["rust_engine_available"] = False
    artifact["engine_parity_status"] = "rust_engine_required_unavailable_fail_closed"
    artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)
    screening_path = _write_screening(tmp_path / "screening_artifact.json", artifact)

    payload = write_hbt0_artifacts(
        repo_root=tmp_path,
        out_dir=tmp_path / "out",
        screening_artifact_path=screening_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        run_id="hbt0_broad_scope_non_rust",
    )

    reasons = payload["replay_summary"]["fail_closed_reasons"]
    assert "screening_artifact_required_rust_engine_missing" in reasons
    assert "screening_artifact_required_rust_engine_unavailable" in reasons


def test_hbt0_requires_promoted_ids_even_when_candidate_id_is_supplied(tmp_path: Path) -> None:
    artifact = _screening_artifact()
    artifact["promoted_ids"] = []
    artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)
    screening_path = _write_screening(tmp_path / "screening_artifact.json", artifact)

    payload = write_hbt0_artifacts(
        repo_root=tmp_path,
        out_dir=tmp_path / "out",
        screening_artifact_path=screening_path,
        candidate_id="cand_hbt0",
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        run_id="hbt0_no_promoted_ids",
    )

    reasons = payload["replay_summary"]["fail_closed_reasons"]
    assert "screening_artifact_has_no_promoted_candidate" in reasons
    assert "candidate_id_not_promoted_by_screening_artifact" in reasons


def test_hbt0_missing_screening_artifact_path_still_writes_fail_closed_summary(
    tmp_path: Path,
) -> None:
    payload = write_hbt0_artifacts(
        repo_root=tmp_path,
        out_dir=tmp_path / "out",
        screening_artifact_path=tmp_path / "missing.json",
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        run_id="hbt0_missing_screening",
    )

    assert Path(payload["source_lock_path"]).is_file()
    assert Path(payload["replay_summary_path"]).is_file()
    assert "screening_artifact_read_failed:FileNotFoundError" in payload["replay_summary"]["fail_closed_reasons"]


def test_hbt0_cli_writes_fail_closed_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "run_hftbacktest_realism.py"
    spec = importlib.util.spec_from_file_location("run_hftbacktest_realism_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    screening_path = _write_screening(tmp_path / "screening_artifact.json")
    out_root = tmp_path / "hbt_realism"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_hftbacktest_realism.py",
            "--screening-artifact",
            str(screening_path),
            "--run-id",
            "cli_hbt0",
            "--repo-root",
            str(tmp_path),
            "--out-root",
            str(out_root),
            "--hftbacktest-upstream-ref",
            "v2.4.2",
            "--native-hot-path-evidence",
            NATIVE_CPP_LATENCY_EVIDENCE,
        ],
    )

    assert module.main() == 2
    assert (out_root / "cli_hbt0" / "hftbacktest_source_lock.json").is_file()
    summary = json.loads((out_root / "cli_hbt0" / "replay_summary.json").read_text(encoding="utf-8"))
    assert summary["replay_realism_status"] == "research_only"


def test_hbt0_code_does_not_name_retired_replay_entrypoints() -> None:
    repo = Path(__file__).resolve().parents[2]
    paths = [
        repo / "packages" / "backtest_pipeline" / "src" / "hftbacktest_realism.py",
        repo / "scripts" / "run_hftbacktest_realism.py",
    ]
    forbidden = ("run_event_universe.py", "run_event_replay.py", "replay_matrix.py", "ReplaySession")
    for path in paths:
        source = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in source
