"""C++ hot-path topology: CMake targets, stack verify harness, colo guards."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from workbench.src.sim.cpp_binary import resolve_cpp_binary
from workbench.src.sim.cpp_stack_verify import (
    CppStackVerifyHarness,
    get_cached_stack_verify,
    reset_stack_verify_cache_for_tests,
    stack_verify_policy,
)


@pytest.fixture(autouse=True)
def _clear_stack_cache() -> None:
    reset_stack_verify_cache_for_tests()
    yield
    reset_stack_verify_cache_for_tests()


def test_cmake_lists_hot_path_targets() -> None:
    root_text = (_REPO / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "hft_rithmic_gateway" in root_text
    assert "hft_research_sim" in root_text
    assert "hft_rithmic_latency_probe" in root_text
    gw_text = (_REPO / "rithmic_gateway" / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "rithmic_gateway/src/rithmic_adapter.cpp" in root_text or "src/rithmic_adapter.cpp" in gw_text


def test_stack_verify_skips_without_binary(tmp_path: Path) -> None:
    harness = CppStackVerifyHarness(repo_root=tmp_path)
    assert harness.binary_exists() is False
    result = harness.verify()
    assert result.stack_verified is False
    assert "cmake" in result.reason.lower() or "build" in result.reason.lower()


def test_stack_verify_parses_json_contract(tmp_path: Path) -> None:
    payload = {
        "stack_verified": True,
        "checks": {
            "gateway_init": True,
            "spsc_queue_roundtrip": True,
            "feature_extract": True,
            "decision_evaluate": True,
            "risk_precheck": True,
        },
        "orders": [],
        "fills": [],
        "latency_logs": [],
    }
    stdout = f"HFT_RESEARCH_SIM_JSON:{json.dumps(payload)}\n"
    fake_bin = tmp_path / "hft_research_sim.exe"
    fake_bin.write_bytes(b"")
    harness = CppStackVerifyHarness(repo_root=tmp_path, engine_binary=fake_bin)

    with patch("workbench.src.sim.cpp_stack_verify.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=stdout, stderr=""
        )
        result = harness.verify()

    assert result.stack_verified is True
    assert result.checks["spsc_queue_roundtrip"] is True
    assert result.subprocess_ran is True


def test_stack_verify_rejects_failed_check_values(tmp_path: Path) -> None:
    payload = {
        "stack_verified": True,
        "checks": {
            "gateway_init": True,
            "spsc_queue_roundtrip": True,
            "feature_extract": True,
            "decision_evaluate": True,
            "risk_precheck": False,
        },
    }
    stdout = f"HFT_RESEARCH_SIM_JSON:{json.dumps(payload)}\n"
    fake_bin = tmp_path / "hft_research_sim.exe"
    fake_bin.write_bytes(b"")
    harness = CppStackVerifyHarness(repo_root=tmp_path, engine_binary=fake_bin)
    with patch("workbench.src.sim.cpp_stack_verify.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=stdout, stderr=""
        )
        result = harness.verify()
    assert result.stack_verified is False
    assert "risk_precheck" in result.reason


def test_stack_verify_rejects_incomplete_checks(tmp_path: Path) -> None:
    payload = {"stack_verified": True, "checks": {"gateway_init": True}}
    stdout = f"HFT_RESEARCH_SIM_JSON:{json.dumps(payload)}\n"
    fake_bin = tmp_path / "hft_research_sim.exe"
    fake_bin.write_bytes(b"")
    harness = CppStackVerifyHarness(repo_root=tmp_path, engine_binary=fake_bin)
    with patch("workbench.src.sim.cpp_stack_verify.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=stdout, stderr=""
        )
        result = harness.verify()
    assert result.stack_verified is False
    assert "missing keys" in result.reason.lower()


def test_stack_verify_rejects_missing_json_line(tmp_path: Path) -> None:
    fake_bin = tmp_path / "hft_research_sim.exe"
    fake_bin.write_bytes(b"")
    harness = CppStackVerifyHarness(repo_root=tmp_path, engine_binary=fake_bin)
    with patch("workbench.src.sim.cpp_stack_verify.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="no json here\n", stderr=""
        )
        result = harness.verify()
    assert result.stack_verified is False
    assert "missing" in result.reason.lower()


def test_cached_stack_verify_runs_once_by_default(tmp_path: Path) -> None:
    from workbench.src.sim.cpp_stack_verify import CppStackVerifyResult

    harness = CppStackVerifyHarness(repo_root=tmp_path, engine_binary=tmp_path / "sim.exe")
    with patch.dict("os.environ", {"HFT3_CPP_STACK_VERIFY": "once"}, clear=False):
        with patch.object(
            harness,
            "verify",
            return_value=CppStackVerifyResult(stack_verified=True, reason="ok"),
        ) as mock_verify:
            first = get_cached_stack_verify(tmp_path, harness=harness)
            second = get_cached_stack_verify(tmp_path, harness=harness)
            assert first.stack_verified is True
            assert second.stack_verified is True
            assert mock_verify.call_count == 1


def test_stack_verify_policy_off() -> None:
    with patch.dict("os.environ", {"HFT3_CPP_STACK_VERIFY": "off"}, clear=False):
        assert stack_verify_policy() == "off"
        result = get_cached_stack_verify(_REPO)
        assert result.stack_verified is False
        assert "disabled" in result.reason.lower()


@pytest.mark.integration
def test_stack_verify_live_binary() -> None:
    exe = resolve_cpp_binary(_REPO, "hft_research_sim")
    if exe is None:
        pytest.skip("hft_research_sim not built (cmake -B build && cmake --build build)")
    with patch.dict("os.environ", {"HFT3_CPP_STACK_VERIFY": "always"}, clear=False):
        harness = CppStackVerifyHarness(repo_root=_REPO)
        result = harness.verify()
    if not result.stack_verified:
        pytest.fail(f"hft_research_sim stack verify failed: {result.reason}")
    assert all(result.checks.values())
