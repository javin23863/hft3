#!/usr/bin/env python3
"""Validate CHI404 tuning gates against PASS_CRITERIA.json."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)


def _tool_available(name: str) -> bool:
    return subprocess.run(["which", name], capture_output=True).returncode == 0


def _check_cyclictest_p99(log_dir: Path, crit: dict) -> list[str]:
    failures: list[str] = []
    limit = crit["cyclictest_p99_max_us"]
    p99_files = sorted(log_dir.glob("cyclictest_cpu*_p99_us"))
    if not p99_files:
        failures.append("G6 cyclictest p99 files missing")
        return failures
    for path in p99_files:
        try:
            p99 = int(path.read_text(encoding="utf-8").strip())
        except ValueError:
            failures.append(f"G6 invalid p99 in {path.name}")
            continue
        cpu = path.name.replace("cyclictest_cpu", "").replace("_p99_us", "")
        if p99 > limit:
            failures.append(f"G6 cpu {cpu} p99={p99}us > {limit}us")
    return failures


def _check_nic_rings(log_dir: Path, crit: dict) -> list[str]:
    failures: list[str] = []
    ring_path = log_dir / "ring_buffer_limitation.json"
    if not ring_path.exists():
        failures.append("G7 ring_buffer_limitation.json missing")
        return failures
    data = json.loads(ring_path.read_text(encoding="utf-8"))
    if data.get("status") == "unknown":
        failures.append(f"G7 ethtool ring status unknown: {data.get('error', 'no detail')}")
        return failures
    ring_min = crit.get("nic_ring_min")
    if ring_min is None:
        return failures
    rx_max = data.get("rx_max")
    tx_max = data.get("tx_max")
    if rx_max is None or tx_max is None:
        failures.append("G7 rx_max/tx_max missing from ring_buffer_limitation.json")
        return failures
    if rx_max < ring_min:
        failures.append(f"G7 rx_max={rx_max} < nic_ring_min={ring_min}")
    if tx_max < ring_min:
        failures.append(f"G7 tx_max={tx_max} < nic_ring_min={ring_min}")
    return failures


def _check_jitter_gate(log_dir: Path) -> list[str]:
    failures: list[str] = []
    jitter = log_dir / "jitter_gate_result"
    if not jitter.exists():
        failures.append("G6 jitter gate result file missing")
        return failures
    text = jitter.read_text(encoding="utf-8")
    if re.search(r"^JITTER_GATE=PASS\b", text, re.MULTILINE):
        return failures
    if "PASS" in text and "JITTER_GATE=FAIL" not in text:
        failures.append("G6 jitter gate not explicitly JITTER_GATE=PASS")
    else:
        failures.append("G6 jitter gate not PASS")
    return failures


def _check_irq_net(log_dir: Path) -> list[str]:
    failures: list[str] = []
    irq_path = log_dir / "irq_net.txt"
    if not irq_path.exists():
        failures.append("G7 irq_net.txt missing")
        return failures
    text = irq_path.read_text(encoding="utf-8", errors="replace")
    if "ethtool -g failed" in text or "status unknown" in text.lower():
        failures.append("G7 irq_net.txt reports ethtool ring failure")
    return failures


def _check_manifest(log_dir: Path) -> list[str]:
    failures: list[str] = []
    manifest = log_dir / "manifest.jsonl"
    if not manifest.exists():
        failures.append("G8 manifest.jsonl missing")
        return failures
    lines = [ln for ln in manifest.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        failures.append("G8 manifest.jsonl is empty")
    return failures


def main() -> int:
    criteria_path = Path(
        sys.argv[1]
        if len(sys.argv) > 1
        else Path(__file__).resolve().parent / "PASS_CRITERIA.json"
    )
    log_dir = Path(
        sys.argv[2]
        if len(sys.argv) > 2
        else os.environ.get("HFT3_TUNING_LOG_DIR", "")
    )
    if not log_dir:
        print("ERROR: set HFT3_TUNING_LOG_DIR or pass log_dir as argv[2]", file=sys.stderr)
        return 1
    if log_dir.name == "latest" and not log_dir.exists():
        print("ERROR: refuse implicit latest log dir; pass explicit RUN_ID path", file=sys.stderr)
        return 1

    crit = json.loads(criteria_path.read_text(encoding="utf-8"))
    failures: list[str] = []

    virt = subprocess.run(
        ["systemd-detect-virt"], capture_output=True, text=True, check=False
    ).stdout.strip()
    if virt != crit["require_virt"]:
        failures.append(f"G1 virt={virt} expected {crit['require_virt']}")

    if not _tool_available("mpstat"):
        failures.append("G2 mpstat not installed — cannot evaluate CPU steal")
    else:
        mp = _run(["mpstat", "1", "5"])
        m = re.search(
            r"Average:\s+all\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+([\d.]+)",
            mp,
        )
        if not m:
            failures.append("G2 could not parse mpstat steal column")
        else:
            steal = float(m.group(1))
            if steal > crit["cpu_steal_max_pct"]:
                failures.append(f"G2 steal={steal}% > {crit['cpu_steal_max_pct']}")

    cmdline = Path("/proc/cmdline").read_text()
    for tok in crit["require_cmdline_tokens"]:
        if tok not in cmdline:
            failures.append(f"G3 missing cmdline token {tok}")

    if not _tool_available("cpupower"):
        failures.append("G4 cpupower not installed — cannot evaluate governor")
    else:
        freq = _run(["cpupower", "frequency-info"])
        gov_match = re.search(r"current policy.*governor\s*:\s*(\S+)", freq, re.IGNORECASE)
        active = gov_match.group(1).lower() if gov_match else ""
        if active != crit["require_governor"].lower():
            failures.append(f"G4 active governor {active!r} != {crit['require_governor']!r}")

    chrony_gate = log_dir / "chrony_gate_result"
    if chrony_gate.exists() and "PASS" in chrony_gate.read_text(encoding="utf-8"):
        pass
    elif not _tool_available("chronyc"):
        failures.append("G5 chronyc not installed and chrony_gate_result missing")
    else:
        track = _run(["chronyc", "tracking"])
        if "Leap status     : Not synchronised" in track:
            failures.append("G5 chrony not synchronised")

        def _to_us(val: float, unit: str) -> float:
            u = unit.lower()
            if u.startswith("ms"):
                return val * 1000.0
            if u.startswith("s") or u == "seconds":
                return val * 1_000_000.0
            return val

        lo = re.search(r"Last offset\s*:\s*([+-]?[\d.]+)\s*(\S+)", track)
        if lo:
            last_us = abs(_to_us(float(lo.group(1)), lo.group(2)))
            lim = crit.get("epsilon_last_offset_halt_us", crit["epsilon_halt_us"])
            if last_us > lim:
                failures.append(f"G5 chrony last offset {last_us:.0f}us > {lim}us")

        rm = re.search(r"RMS offset\s*:\s*([\d.]+)\s*(\S+)", track)
        if rm:
            rms_us = abs(_to_us(float(rm.group(1)), rm.group(2)))
            rlim = crit.get("epsilon_rms_halt_us", crit["epsilon_halt_us"] * 10)
            if rms_us > rlim:
                failures.append(f"G5 chrony RMS {rms_us:.0f}us > {rlim}us")

    failures.extend(_check_jitter_gate(log_dir))
    failures.extend(_check_cyclictest_p99(log_dir, crit))
    failures.extend(_check_irq_net(log_dir))
    failures.extend(_check_nic_rings(log_dir, crit))
    failures.extend(_check_manifest(log_dir))

    out = log_dir / "PASS_FAIL.txt"
    if failures:
        out.write_text("FAIL\n" + "\n".join(failures) + "\n", encoding="utf-8")
        print("FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1

    out.write_text("PASS\nAll gates satisfied.\n", encoding="utf-8")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
