"""Guardrails: CHI404 canonical path only; no synthetic paper order inject in active scripts."""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
DEPRECATED = SCRIPTS / "deprecated"
CANONICAL_DOC = REPO / "docs" / "vault" / "CHI404_CANONICAL_ENTRYPOINTS.md"

FORBIDDEN_PATTERNS = [
    re.compile(r"Add-Content.*order_submit", re.I),
    re.compile(r"f\.write\(f[\"'].*order_submit", re.I),
    re.compile(r"SWEEP-\{batch", re.I),
    re.compile(r"MKT-\{batch", re.I),
]

ALLOWLIST: set[str] = set()


def _active_script_paths() -> list[Path]:
    out: list[Path] = []
    for p in SCRIPTS.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(SCRIPTS)
        if rel.parts[0] == "deprecated":
            continue
        if p.suffix.lower() not in {".sh", ".ps1", ".py"}:
            continue
        out.append(p)
    return out


def test_canonical_entrypoints_doc_exists() -> None:
    assert CANONICAL_DOC.is_file(), "missing docs/vault/CHI404_CANONICAL_ENTRYPOINTS.md"


def test_graphify_gate_scripts_exist() -> None:
    assert (SCRIPTS / "graphify_gate.ps1").is_file()
    assert (SCRIPTS / "graphify_gate.sh").is_file()


def test_active_scripts_no_synthetic_order_inject() -> None:
    violations: list[str] = []
    for path in _active_script_paths():
        if path.name in ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pat in FORBIDDEN_PATTERNS:
            if pat.search(text):
                violations.append(f"{path.relative_to(REPO)}: {pat.pattern}")
    assert not violations, "synthetic order inject in active scripts:\n" + "\n".join(violations)


def test_no_rtrader_active_scripts() -> None:
    """R|Trader Windows VM is gone. Active scripts must not reference its bridge."""
    rtrader_files = [
        "chi404_vm_deploy.sh",
        "chi404_finish_rtrader.sh",
        "chi404_vm_apply_headless.py",
        "chi404_vm_restart_rtrader.py",
        "chi404_trigger_vm_paper_sweep.py",
        "chi404_vm_run_interactive.py",
        "chi404_vm_paper_order_sweep.ps1",
    ]
    survivors = [n for n in rtrader_files if (SCRIPTS / n).is_file()]
    assert not survivors, f"R|Trader VM scripts must be deleted: {survivors}"


def test_reprocess_sweep_log_is_stub() -> None:
    """Only allowed if file was moved to scripts/deprecated or marked as such."""
    stub = SCRIPTS / "chi404_reprocess_sweep_log.py"
    if not stub.is_file():
        return
    text = stub.read_text(encoding="utf-8")
    assert "deprecated" in text.lower() or "moved to scripts/deprecated" in text.lower()
    assert "PaperLatencyRecordV1" not in text


def test_sweep_orchestrator_no_virsh_fallback() -> None:
    sh = (SCRIPTS / "chi404_run_paper_latency_sweep.sh").read_text(encoding="utf-8")
    assert "virsh" not in sh, "orchestrator must not depend on libvirt (VM is gone)"
    assert "rithmic_latency_probe" in sh
    assert "hot_path_language=c++" in sh
    assert "wrapper=none" in sh
    assert "python3 -m data_system.rithmic_trial.pipeline paper-latency-daemon" not in sh


def test_no_windows_only_doc_asserts_chi404() -> None:
    """Docs must not assert 'Windows runs the trade path' anywhere."""
    forbidden_substrings = [
        "Windows is the only trade-path host",
        "Windows VM is the only place",
    ]
    for path in REPO.rglob("docs/**/*.md"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for s in forbidden_substrings:
            assert s not in text, f"{path.relative_to(REPO)} still asserts Windows-only: {s}"


def test_canonical_entrypoints_doc_uses_rithmic_api() -> None:
    text = CANONICAL_DOC.read_text(encoding="utf-8")
    assert "rithmic_api" in text or "R|API+" in text, (
        "CHI404_CANONICAL_ENTRYPOINTS.md must document the rithmic_api connector path"
    )
    assert "hft3-rithmic-trial.service" in text
