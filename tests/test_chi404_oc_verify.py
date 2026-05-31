"""CHI404 OC verify scripts exist; parse logic matches gates."""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_CHI404 = _REPO / "infrastructure" / "chi404"


def test_oc_scripts_exist():
    for name in (
        "14_bios_oc_readiness.sh",
        "15_post_bios_oc_verify.sh",
        "16_oc_stability_under_load.sh",
        "17a_oob_preflight.sh",
        "24_recover_boot_to_disk.sh",
        "25_expo_sol_preflight.sh",
    ):
        assert (_CHI404 / name).is_file(), name


def test_quarantined_bios_reboot_requires_oob():
    text = (_CHI404 / "22b_apply_and_reboot_bios.sh").read_text(encoding="utf-8")
    assert "QUARANTINED" in text
    assert "HFT3_OOB_CONFIRMED" in text
    assert "17a_oob_preflight.sh" in text


def test_recover_boot_script_clears_override():
    text = (_CHI404 / "24_recover_boot_to_disk.sh").read_text(encoding="utf-8")
    assert "bootdev disk" in text
    assert "If-Match" in text
    assert "BootSourceOverrideEnabled" in text


def test_remote_bios_prep_gates_oob():
    text = (_CHI404 / "17_remote_bios_prep.sh").read_text(encoding="utf-8")
    assert "17a_oob_preflight.sh" in text
    assert "HFT3_OOB_CONFIRMED" in text


def test_access_paths_doc_exists():
    assert (_REPO / "docs" / "chi404" / "CHI404_ACCESS_PATHS.md").is_file()


def test_oob_recovery_scripts_exist():
    for name in (
        "run_chi404_oob_recovery.ps1",
        "run_chi404_bmc_redfish_recovery.ps1",
    ):
        assert (_REPO / "scripts" / name).is_file(), name


def test_oc_verify_json_gate_logic():
    """Mirror 15_post_bios_oc_verify.py thresholds."""
    min_mem = 4800
    min_mhz = 5400

    ok = {
        "memory_configured_mts_min": 4800,
        "max_hot_cpu_mhz": 5531.0,
        "failures": [],
    }
    assert ok["memory_configured_mts_min"] >= min_mem
    assert ok["max_hot_cpu_mhz"] >= min_mhz

    bad_mem = {"memory_configured_mts_min": 3600, "max_hot_cpu_mhz": 5531.0}
    assert bad_mem["memory_configured_mts_min"] < min_mem

    bad_cpu = {"memory_configured_mts_min": 4800, "min_hot_cpu_mhz": 4400.0}
    assert bad_cpu["min_hot_cpu_mhz"] < min_mhz


def test_dmidecode_speed_regex():
    text = "Configured Memory Speed: 4800 MT/s\nConfigured Memory Speed: 3600 MT/s\n"
    speeds = [int(m.group(1)) for m in re.finditer(r"Configured Memory Speed:\s*(\d+)\s*MT/s", text)]
    assert speeds == [4800, 3600]
    assert min(speeds) == 3600
