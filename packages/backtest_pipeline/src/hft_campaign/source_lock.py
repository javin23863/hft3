"""Resolve campaign source lock and native hot-path evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backtest_pipeline.src.hft_campaign._hashing import sha256_file
from backtest_pipeline.src.hftbacktest_realism import (
    _looks_like_native_cpp_hot_path_evidence,
    build_hftbacktest_source_lock,
    validate_hftbacktest_source_lock,
)


def _native_hot_path_evidence_paths(repo_root: Path) -> list[str]:
    root = Path(repo_root)
    evidence_roots = (
        root / "reports" / "latency_baselines",
        root / "runtime" / "latency_reports",
        root / "reports" / "cpp_lane",
        root / "runtime" / "cpp_lane",
        root / "runtime" / "reports",
        root / "research_cards" / "cpp_lane",
        root / "research_cards" / "hftbacktest_realism",
    )
    evidence: list[str] = []
    for evidence_root in evidence_roots:
        if not evidence_root.is_dir():
            continue
        candidates = sorted(
            (path for path in evidence_root.rglob("*") if path.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for path in candidates:
            rel = str(path.relative_to(root)).replace("\\", "/")
            if not _looks_like_native_cpp_hot_path_evidence(rel):
                continue
            digest = sha256_file(path)
            evidence.append(f"{rel}#sha256:{digest}")
    return evidence


def build_campaign_source_lock(repo_root: Path) -> tuple[dict[str, Any], list[str]]:
    evidence = _native_hot_path_evidence_paths(repo_root)
    lock = build_hftbacktest_source_lock(
        repo_root=repo_root,
        native_hot_path_evidence=evidence or None,
        native_hot_path_status="provided" if evidence else "missing",
    )
    reasons = validate_hftbacktest_source_lock(lock)
    return lock, reasons
