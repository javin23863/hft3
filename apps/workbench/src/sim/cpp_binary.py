"""Resolve built C++ binaries under repo build/."""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def resolve_cpp_binary(repo_root: Path, base_name: str) -> Optional[Path]:
    """Find hft_* executable under build/ (Release/Debug or flat)."""
    repo_root = Path(repo_root)
    candidates = []
    for sub in ("", "Release", "Debug"):
        root = repo_root / "build" / sub if sub else repo_root / "build"
        for suffix in ("", ".exe"):
            candidates.append(root / f"{base_name}{suffix}")
    for path in candidates:
        if path.is_file():
            return path
    return None
