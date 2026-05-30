"""Tests for CHI404 VM WinRM helper (no live WinRM required)."""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"


def _run_helper_snippet(code: str, env: dict | None = None) -> subprocess.CompletedProcess:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=SCRIPTS,
        env=merged,
        capture_output=True,
        text=True,
    )


def test_require_vm_password_exits_when_missing():
    r = _run_helper_snippet(
        "import sys; sys.path.insert(0, '.'); "
        "from chi404_vm_winrm import require_vm_password; require_vm_password()",
        env={k: v for k, v in os.environ.items() if k != "VM_ADMIN_PASSWORD"},
    )
    assert r.returncode == 1
    assert "VM_ADMIN_PASSWORD is required" in r.stderr


def test_require_vm_password_returns_value():
    r = _run_helper_snippet(
        "import sys; sys.path.insert(0, '.'); "
        "from chi404_vm_winrm import require_vm_password; "
        "print(require_vm_password())",
        env={"VM_ADMIN_PASSWORD": "test-secret"},
    )
    assert r.returncode == 0
    assert r.stdout.strip() == "test-secret"
