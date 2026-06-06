"""Governance unit tests for tiered backtester certification."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from hft3.validation.certification_registry import CertificationRecord, load_registry, save_registry
from hft3.validation.certification_staleness import assess_staleness
from hft3.validation.fast_gate_report import load_fast_gate_report, write_fast_gate_report
from hft3.validation.promotion_gate import evaluate_promotion_gate
from hft3.validation.research_stamp import build_certification_stamp


def test_stamp_green_no_change_promotion_eligible(tmp_path: Path) -> None:
    reg = CertificationRecord(
        latest_certification_run_id="CERT-test",
        latest_certification_commit="abc123",
        latest_certification_status="GREEN",
        backtester_version="v1",
    )
    save_registry(reg, tmp_path)
    with patch("hft3.validation.research_stamp.repo_root", return_value=tmp_path):
        with patch("hft3.validation.certification_staleness.git_sha", return_value="abc123"):
            with patch("hft3.validation.certification_staleness._git_diff_files", return_value=[]):
                with patch("hft3.validation.certification_staleness._commit_is_ancestor", return_value=True):
                    stamp = build_certification_stamp()
    assert stamp["promotion_eligible"] is True
    assert stamp["promotion_label"] == "PROMOTION_ELIGIBLE_FROM_BACKTESTER_SIDE"


def test_stamp_green_core_change_stale(tmp_path: Path) -> None:
    reg = CertificationRecord(
        latest_certification_run_id="CERT-test",
        latest_certification_commit="abc123",
        latest_certification_status="GREEN",
    )
    save_registry(reg, tmp_path)
    with patch("hft3.validation.research_stamp.repo_root", return_value=tmp_path):
        with patch("hft3.validation.certification_staleness.git_sha", return_value="def456"):
            with patch(
                "hft3.validation.certification_staleness._git_diff_files",
                return_value=["replay/replay_session.py"],
            ):
                with patch("hft3.validation.certification_staleness._commit_is_ancestor", return_value=True):
                    stamp = build_certification_stamp()
    assert stamp["stale"] is True
    assert stamp["promotion_label"] == "STALE_CERTIFICATION"
    assert stamp["promotion_eligible"] is False


def test_staleness_missing_registry(tmp_path: Path) -> None:
    result = assess_staleness(tmp_path)
    assert result.certification_is_current is False
    assert result.stale_reason == "no_full_certification_on_record"


def test_registry_round_trip(tmp_path: Path) -> None:
    reg = CertificationRecord(
        latest_certification_run_id="CERT-1",
        latest_certification_commit="deadbeef",
        latest_certification_status="YELLOW",
        warnings=["partial coverage"],
    )
    save_registry(reg, tmp_path)
    loaded = load_registry(tmp_path)
    assert loaded.latest_certification_run_id == "CERT-1"
    assert loaded.latest_certification_status == "YELLOW"
    assert loaded.warnings == ["partial coverage"]


def test_promotion_gate_fails_on_missing_green(tmp_path: Path) -> None:
    save_registry(CertificationRecord(), tmp_path)
    with patch("hft3.validation.promotion_gate.repo_root", return_value=tmp_path):
        with patch("hft3.validation.promotion_gate._run_t0_fast_gate", return_value=(True, "")):
            result = evaluate_promotion_gate(event_id="CPI_2024", symbol="ES")
    assert result.passed is False
    assert any("GREEN" in f or "MISSING" in f or "missing" in f for f in result.failures)


def test_promotion_gate_fails_on_red(tmp_path: Path) -> None:
    save_registry(
        CertificationRecord(
            latest_certification_commit="abc",
            latest_certification_status="RED",
        ),
        tmp_path,
    )
    scorecard = tmp_path / "runtime/validation/backtester_certification_scorecard.json"
    scorecard.parent.mkdir(parents=True, exist_ok=True)
    scorecard.write_text(json.dumps({"status": "RED"}), encoding="utf-8")
    with patch("hft3.validation.promotion_gate.repo_root", return_value=tmp_path):
        with patch("hft3.validation.promotion_gate._run_t0_fast_gate", return_value=(True, "")):
            result = evaluate_promotion_gate()
    assert result.passed is False


def test_fast_gate_report_writer(tmp_path: Path) -> None:
    path = write_fast_gate_report(
        passed=True,
        duration_sec=1.5,
        test_count=10,
        root=tmp_path,
    )
    assert path.is_file()
    loaded = load_fast_gate_report(tmp_path)
    assert loaded is not None
    assert loaded["passed"] is True
    assert loaded["test_count"] == 10


def test_full_suite_single_failure_is_red_blocker(tmp_path: Path, monkeypatch) -> None:
    from hft3.validation import certification_runner as cr

    def fake_run_pytest(target: str, root: Path) -> tuple[bool, str, int, int, float]:
        if target == "tests/backtester_validation/fast":
            return True, "1 passed", 1, 0, 0.1
        if target == "tests/backtester_validation/full":
            return False, "FAILED tests/backtester_validation/full/test_cert.py::test_case\n1 failed", 1, 1, 0.1
        raise AssertionError(f"unexpected pytest target: {target}")

    monkeypatch.setattr(cr, "_run_pytest", fake_run_pytest)
    monkeypatch.setattr(cr, "git_sha", lambda root: "abc123")
    monkeypatch.setattr(cr, "backtester_version", lambda root: "v-test")
    monkeypatch.setattr(cr, "new_certification_run_id", lambda: "CERT-test-full-fail")

    result = cr.run_full_certification(tmp_path, skip_lane_pytest=True)

    assert result.status == "RED"
    assert result.blocking_failures == ["T2 full certification suite failed"]

    payload = json.loads(Path(result.scorecard_json).read_text(encoding="utf-8"))
    assert payload["status"] == "RED"
    assert payload["full_passed"] is False
    assert payload["blocking_failures"] == ["T2 full certification suite failed"]

    registry = load_registry(tmp_path)
    assert registry.latest_certification_status == "RED"
    assert registry.blocking_failures == ["T2 full certification suite failed"]


def test_certification_runner_main_exit_code(monkeypatch) -> None:
    from hft3.validation import certification_runner as cr

    class _Result:
        status = "YELLOW"
        run_id = "CERT-test"
        blocking_failures: list[str] = []

    monkeypatch.setattr(cr, "run_full_certification", lambda root=None: _Result())
    assert cr.main() == 1

    _Result.status = "GREEN"
    assert cr.main() == 0
