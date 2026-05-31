"""Detect core backtester code changes since last GREEN certification."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from hft3.validation.certification_registry import CertificationRecord, git_sha, load_registry
from hft3.validation.core_engine_paths import CORE_BACKTESTER_PATHS


@dataclass
class StalenessResult:
    certification_is_current: bool
    current_commit: str
    certification_commit: str
    changed_core_files: list[str] = field(default_factory=list)
    stale_reason: str = ""


def _git_diff_files(base: str, head: str, root: Path) -> list[str]:
    if not base or base == "unknown" or not head or head == "unknown":
        return []
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", f"{base}..{head}", "--", *CORE_BACKTESTER_PATHS],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return [line.strip().replace("\\", "/") for line in out.splitlines() if line.strip()]
    except subprocess.CalledProcessError:
        return []


def _commit_is_ancestor(ancestor: str, head: str, root: Path) -> bool:
    if not ancestor or ancestor == "unknown" or not head or head == "unknown":
        return False
    try:
        subprocess.check_call(
            ["git", "merge-base", "--is-ancestor", ancestor, head],
            cwd=str(root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def assess_staleness(
    repo_root: Path,
    current_commit: str | None = None,
    registry: CertificationRecord | None = None,
) -> StalenessResult:
    registry = registry or load_registry(repo_root)
    current = current_commit or git_sha(repo_root)
    cert_commit = registry.latest_certification_commit

    if registry.latest_certification_status == "MISSING" or not cert_commit:
        return StalenessResult(
            certification_is_current=False,
            current_commit=current,
            certification_commit=cert_commit,
            stale_reason="no_full_certification_on_record",
        )

    if registry.latest_certification_status != "GREEN":
        return StalenessResult(
            certification_is_current=False,
            current_commit=current,
            certification_commit=cert_commit,
            stale_reason=f"latest_certification_status={registry.latest_certification_status}",
        )

    changed = _git_diff_files(cert_commit, current, repo_root)
    if changed:
        return StalenessResult(
            certification_is_current=False,
            current_commit=current,
            certification_commit=cert_commit,
            changed_core_files=changed,
            stale_reason="core_backtester_files_changed_since_certification",
        )

    if not _commit_is_ancestor(cert_commit, current, repo_root):
        return StalenessResult(
            certification_is_current=False,
            current_commit=current,
            certification_commit=cert_commit,
            stale_reason="certification_commit_not_ancestor_of_head",
        )

    return StalenessResult(
        certification_is_current=True,
        current_commit=current,
        certification_commit=cert_commit,
    )
