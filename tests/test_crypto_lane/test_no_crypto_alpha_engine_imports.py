"""Fail if crypto_lane imports legacy crypto-alpha-engine modules."""
from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_MODULES = (
    "crypto_alpha",
    "crypto_alpha_engine",
)

FORBIDDEN_SYMBOLS = (
    "CryptoAlphaStrategy",
    "ExecutionRouter",
    "GateStack",
)

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [
    ROOT / "packages" / "crypto_lane",
    ROOT / "tests" / "test_crypto_lane",
]


def _imports_in_file(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_no_forbidden_imports():
    violations: list[str] = []
    for base in SCAN_DIRS:
        for path in base.rglob("*.py"):
            if path.name == "test_no_crypto_alpha_engine_imports.py":
                continue
            for mod in _imports_in_file(path):
                for token in FORBIDDEN_MODULES:
                    if mod == token or mod.startswith(token + "."):
                        violations.append(f"{path}: imports {mod}")
            text = path.read_text(encoding="utf-8")
            for sym in FORBIDDEN_SYMBOLS:
                if sym in text:
                    violations.append(f"{path}: references forbidden symbol {sym}")
    assert not violations, "\n".join(violations)
