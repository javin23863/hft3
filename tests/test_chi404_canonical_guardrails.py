"""Guardrails: CHI404 canonical path only; no synthetic paper order inject in active scripts."""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
DEPRECATED = SCRIPTS / "deprecated"
CANONICAL_DOC = REPO / "docs" / "vault" / "CHI404_CANONICAL_ENTRYPOINTS.md"

# Active scripts must not fake R|Trader order export lines.
FORBIDDEN_PATTERNS = [
    re.compile(r"Add-Content.*order_submit", re.I),
    re.compile(r"f\.write\(f[\"'].*order_submit", re.I),
    re.compile(r"SWEEP-\{batch", re.I),
    re.compile(r"MKT-\{batch", re.I),
]

ALLOWLIST = {
    "chi404_vm_winrm.py",  # base64 upload chunks only
}


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


def test_paper_sweep_ps1_no_synthetic_log_inject() -> None:
    ps1 = SCRIPTS / "chi404_vm_paper_order_sweep.ps1"
    text = ps1.read_text(encoding="utf-8")
    assert "Add-Content" not in text, "paper sweep must not write fake order log lines"
    assert "rtrader_ui_real" in text, "paper sweep must declare real UI mode"


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


def test_vm_deploy_chain_scripts_exist() -> None:
    required = [
        "chi404_vm_deploy.sh",
        "chi404_finish_rtrader.sh",
        "chi404_run_trial_live.sh",
        "chi404_run_paper_latency_sweep.sh",
        "chi404_vm_apply_headless.py",
        "chi404_vm_restart_rtrader.py",
        "chi404_trigger_vm_paper_sweep.py",
        "chi404_vm_run_interactive.py",
    ]
    missing = [n for n in required if not (SCRIPTS / n).is_file()]
    assert not missing, f"missing canonical CHI404 scripts: {missing}"


def test_reprocess_sweep_log_is_stub() -> None:
    stub = SCRIPTS / "chi404_reprocess_sweep_log.py"
    assert stub.is_file()
    text = stub.read_text(encoding="utf-8")
    assert "deprecated" in text.lower() or "moved to scripts/deprecated" in text.lower()
    assert "PaperLatencyRecordV1" not in text


def test_sweep_orchestrator_no_virsh_fallback() -> None:
    sh = (SCRIPTS / "chi404_run_paper_latency_sweep.sh").read_text(encoding="utf-8")
    assert "virsh qemu-agent-command" not in sh
    assert "--min-paired" in sh


def test_restart_rtrader_uses_interactive_login() -> None:
    py = (SCRIPTS / "chi404_vm_restart_rtrader.py").read_text(encoding="utf-8")
    assert "chi404_vm_run_interactive" in py
    assert "HFT3-RithmicLoginOnce" in py
    assert r"File C:\\chi404_vm_rtrader_login.ps1" not in py
