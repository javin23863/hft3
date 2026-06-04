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


def test_latency_probe_authority_is_native_cpp_not_capi_wrapper() -> None:
    src = (_REPO / "rithmic_gateway" / "tools" / "rithmic_latency_probe.cpp").read_text(
        encoding="utf-8"
    )
    c_api = (_REPO / "rithmic_gateway" / "src" / "c_api.cpp").read_text(encoding="utf-8")
    cmake = (_REPO / "rithmic_gateway" / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "rithmic_latency_probe" in cmake
    assert "rithmic_capi_latency_probe" not in cmake
    assert not (_REPO / "rithmic_gateway" / "tools" / "rithmic_capi_latency_probe.cpp").exists()
    assert '#include "rithmic_adapter.hpp"' in src
    assert '#include "c_api.hpp"' not in src
    assert "steady_now_ns()" in src
    assert "order_event_matches" in src
    assert "send_prepared_limit_order(" in src
    assert "send_bound_prepared_limit_order" not in src
    assert "bind_prepared_limit_order_static_fields" not in src
    assert '\\"hot_path_language\\": \\"c++\\"' in src
    assert '\\"wrapper\\": \\"none\\"' in src
    assert "tick_to_send_trigger_us" in src
    assert "placement_trigger_kpi" in src
    assert "apply_runtime_tuning" in src
    assert "runtime_tuning" in src
    assert "RITHMIC_PROBE_CPU" in src
    assert "RITHMIC_PROBE_RT_PRIORITY" in src
    assert "RITHMIC_PROBE_MLOCK" in src
    assert "RITHMIC_PROBE_PREFAULT_BYTES" in src
    assert "RITHMIC_PROBE_SUBSCRIBE_FOR_ORDER_EVENTS" in src
    assert "RITHMIC_PROBE_POST_SUBSCRIBE_WARM_MS" in src
    assert "order-event warming" in src
    assert "sched_setaffinity" in src or "SetProcessAffinityMask" in src
    assert "sched_setscheduler" in src or "SetPriorityClass" in src
    assert "mlockall" in src
    assert "while (mbo_queue->pop(stale_md))" not in src
    assert src.index("native_baseline_start") < src.index(
        "if ((require_md || subscribe_for_order_events) && !subscribed_mbo)"
    )
    assert "primed_for_sample=1" in src
    assert "primed_md_available = false" in src
    assert "adapter->send_order" not in c_api
    assert "adapter->cancel_order" not in c_api
    assert "C_API_ORDER_SEND_DISABLED_USE_NATIVE_CPP_PROBE" in c_api
    assert "C_API_CANCEL_DISABLED_USE_NATIVE_CPP_PROBE" in c_api


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
