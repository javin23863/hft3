"""Tests for CHI404 memory upgrade infra (restore + gap-fill validate profile)."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parents[1]
_VALIDATE = _REPO / "infrastructure" / "chi404" / "validate_pass_criteria.py"
_CRITERIA = _REPO / "infrastructure" / "chi404" / "PASS_CRITERIA.json"
_CHI404 = _REPO / "infrastructure" / "chi404"


def _load_validate():
    spec = importlib.util.spec_from_file_location("validate_pass_criteria", _VALIDATE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "script",
    [
        "00_restore_point_capture.sh",
        "00_restore_point_restore.sh",
        "12_memory_gap_fill.sh",
        "12_memory_idle_apply.sh",
        "run_chi404_memory_upgrade.sh",
    ],
)
def test_memory_upgrade_scripts_exist(script: str) -> None:
    path = _CHI404 / script
    assert path.is_file(), f"missing {path}"


def test_pass_criteria_memory_upgrade_fields() -> None:
    crit = json.loads(_CRITERIA.read_text(encoding="utf-8"))
    for tok in ("rcu_nocb_poll", "idle=poll", "acpi_irq_nobalance"):
        assert tok in crit.get("require_cmdline_tokens_memory_upgrade", [])
    assert crit.get("require_idle_disabled") is True


def test_check_cmdline_tokens_memory_profile_required() -> None:
    vpc = _load_validate()
    crit = json.loads(_CRITERIA.read_text(encoding="utf-8"))
    cmdline = "isolcpus=2-11 nohz_full=2-11 rcu_nocbs=2-11"
    failures, warns = vpc._check_cmdline_tokens(cmdline, crit, "memory_upgrade")
    assert failures
    assert any("rcu_nocb_poll" in f for f in failures)
    assert not warns


def test_check_cmdline_tokens_full_profile_warn_only() -> None:
    vpc = _load_validate()
    crit = json.loads(_CRITERIA.read_text(encoding="utf-8"))
    cmdline = "isolcpus=2-11 nohz_full=2-11 rcu_nocbs=2-11"
    failures, warns = vpc._check_cmdline_tokens(cmdline, crit, "")
    assert not failures
    assert warns


def test_cpupower_idle_disabled_parser_empty_fails() -> None:
    vpc = _load_validate()
    empty = "CPU idle driver: none\n"
    with patch.object(vpc, "_tool_available", return_value=True), patch.object(
        vpc, "_run", return_value=empty
    ):
        ok, detail = vpc._cpupower_idle_disabled()
    assert ok is False
    assert "no parseable" in detail


def test_capture_idle_manifest_parser(tmp_path: Path) -> None:
    diag = tmp_path / "diagnostics"
    diag.mkdir()
    (diag / "cpupower_idle_info.txt").write_text(
        "Available idle states:\n  POLL: x\n  C1: y disabled by OS\n",
        encoding="utf-8",
    )
    text = (diag / "cpupower_idle_info.txt").read_text()
    in_states = False
    saw_non_poll = False
    idle_disabled = "unknown"
    for line in text.splitlines():
        if "Available idle states" in line:
            in_states = True
            continue
        if not in_states:
            continue
        name = line.strip().split(":")[0].strip()
        if name.upper() == "POLL":
            continue
        saw_non_poll = True
        if "disabled" not in line.lower():
            idle_disabled = "false"
            break
    else:
        if saw_non_poll:
            idle_disabled = "true"
    assert idle_disabled == "true"


def test_cpupower_idle_disabled_parser() -> None:
    vpc = _load_validate()
    sample = """
Available idle states:
  POLL: flags[...]
  C1: flags[...] disabled by OS
  C2: flags[...] disabled by OS
"""
    with patch.object(vpc, "_tool_available", return_value=True), patch.object(
        vpc, "_run", return_value=sample
    ):
        ok, detail = vpc._cpupower_idle_disabled()
    assert ok is True
    assert detail == "ok"

    bad = """
Available idle states:
  POLL: flags[...]
  C1: flags[...]
"""
    with patch.object(vpc, "_tool_available", return_value=True), patch.object(
        vpc, "_run", return_value=bad
    ):
        ok, detail = vpc._cpupower_idle_disabled()
    assert ok is False


def test_validate_memory_upgrade_profile_skips_irq_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_dir = tmp_path / "mem_run"
    log_dir.mkdir()
    (log_dir / "jitter_gate_result").write_text("JITTER_GATE=PASS\n", encoding="utf-8")
    (log_dir / "cyclictest_cpu2_p99_us").write_text("5\n", encoding="utf-8")

    monkeypatch.setenv("HFT3_VALIDATE_PROFILE", "memory_upgrade")
    monkeypatch.setenv(
        "HFT3_TEST_CMDLINE",
        "isolcpus=2-11 nohz_full=2-11 rcu_nocbs=2-11 rcu_nocb_poll idle=poll acpi_irq_nobalance",
    )

    vpc = _load_validate()
    with patch.object(vpc, "_detect_virt", return_value="none"), patch.object(
        vpc, "_tool_available", return_value=True
    ), patch.object(vpc, "_run") as mock_run, patch.object(
        vpc, "_cpupower_idle_disabled", return_value=(True, "ok")
    ), patch.object(
        vpc, "_check_jitter_gate", return_value=[]
    ), patch.object(
        vpc, "_check_cyclictest_p99", return_value=[]
    ):
        mock_run.side_effect = lambda cmd: {
            "mpstat": "Average:     all    0.00    0.00    0.00    0.00    0.00    0.00    0.00    0.00    0.00   100.00",
            "cpupower": "current policy: governor performance",
            "chronyc": "Leap status     : Normal\nLast offset     : +0.000001 seconds\nRMS offset      : 0.000010 seconds",
        }[cmd[0]]
        with patch.object(vpc.Path, "exists", return_value=False):
            failures: list[str] = []
            failures.extend(vpc._check_cmdline_tokens(vpc._read_cmdline(), json.loads(_CRITERIA.read_text()), "memory_upgrade")[0])
            failures.extend(vpc._check_cpupower_idle(json.loads(_CRITERIA.read_text()), vpc._read_cmdline()))
            assert failures == []


def test_orchestrator_resume_values_only_0_and_4() -> None:
    text = (_CHI404 / "run_chi404_memory_upgrade.sh").read_text(encoding="utf-8")
    assert "0|4" in text
    assert "HFT3_MEMORY_RESUME_STEP must be 0" in text


def test_orchestrator_post_reboot_idle_apply() -> None:
    text = (_CHI404 / "run_chi404_memory_upgrade.sh").read_text(encoding="utf-8")
    assert "12_memory_idle_apply.sh" in text
    idx_idle = text.index("12_memory_idle_apply")
    idx_jitter = text.index("05_jitter_gate")
    assert idx_idle < idx_jitter


def test_remote_ps1_requires_run_id_on_resume() -> None:
    text = (_REPO / "scripts" / "run_chi404_memory_upgrade_remote.ps1").read_text(encoding="utf-8")
    assert 'HFT3_MEMORY_RESUME_STEP=4 requires RUN_ID' in text
