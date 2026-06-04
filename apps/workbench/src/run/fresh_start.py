"""Fresh all-lane run boundary and generated-artifact cleanup."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable


GENERATED_TARGETS = (
    "runtime/workbench/crypto_smoke",
    "runtime/workbench/feature_fabric",
    "runtime/workbench/screenshots",
    "runtime/research",
    "runtime/reports/full_pipeline_gate.json",
    "research_cards/crypto",
    "research_cards/pipeline_runs",
    "research_cards/workbench_runs",
    "research_cards/promotion",
    "artifacts/research_cards/workbench_runs",
    "artifacts/runs",
)

PRESERVED_PREFIXES = (
    "data",
    "apps",
    "packages",
    "scripts",
    "docs",
    "catalogs",
    "configs",
    "config",
    ".git",
    ".env",
    "wallet",
    "wallets",
    "runtime/wallet_setup",
    "research_cards/kg",
    "reports/latency_baselines",
)


class FreshStartError(RuntimeError):
    """Raised when the fresh-start cleanup would be unsafe."""


FreshStartRefusal = FreshStartError


@dataclass(frozen=True)
class DeleteCandidate:
    path: Path
    relative_path: str
    kind: str
    bytes: int
    mtime: float


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _default_run_id() -> str:
    return "all_lanes_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _relative(repo: Path, path: Path) -> str:
    return path.resolve().relative_to(repo.resolve()).as_posix()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _tracked_paths(repo: Path) -> set[str]:
    if not (repo / ".git").exists():
        return set()
    try:
        proc = subprocess.run(
            ["git", "ls-files"],
            cwd=str(repo),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        raise FreshStartError("Refusing cleanup because git tracked-file check failed")
    return {line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()}


def _path_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    if path.is_dir():
        for child in path.rglob("*"):
            if child.is_file():
                try:
                    total += child.stat().st_size
                except OSError:
                    pass
    return total


def _is_tracked(
    repo: Path,
    path: Path,
    tracked: set[str],
    tracked_checker: Callable[[Path], bool] | None,
) -> bool:
    if tracked_checker is not None:
        return bool(tracked_checker(path))
    return _relative(repo, path) in tracked


def _iter_delete_candidates(
    repo: Path,
    target: Path,
    tracked: set[str],
    tracked_checker: Callable[[Path], bool] | None = None,
) -> Iterable[DeleteCandidate]:
    if not target.exists():
        return
    if target.is_file():
        rel = _relative(repo, target)
        if _is_tracked(repo, target, tracked, tracked_checker):
            raise FreshStartError(f"Refusing to delete tracked file: {rel}")
        stat = target.stat()
        yield DeleteCandidate(target, rel, "file", stat.st_size, stat.st_mtime)
        return
    for child in sorted(target.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if not _is_relative_to(child, repo):
            raise FreshStartError(f"Refusing target outside repo: {child.resolve()}")
        rel = _relative(repo, child)
        if _is_tracked(repo, child, tracked, tracked_checker):
            continue
        if child.is_file():
            stat = child.stat()
            yield DeleteCandidate(child, rel, "file", stat.st_size, stat.st_mtime)
        elif child.is_dir():
            yield DeleteCandidate(child, rel, "dir", _path_size(child), child.stat().st_mtime)
    rel = _relative(repo, target)
    if not _is_tracked(repo, target, tracked, tracked_checker):
        yield DeleteCandidate(target, rel, "dir", _path_size(target), target.stat().st_mtime)


def _validate_target(repo: Path, target: Path) -> None:
    resolved = target.resolve()
    if not _is_relative_to(resolved, repo):
        raise FreshStartError(f"Refusing target outside repo: {resolved}")
    rel = _relative(repo, resolved)
    first = rel.split("/", 1)[0]
    if rel in {".", ""} or first in {".git"}:
        raise FreshStartError(f"Refusing unsafe target: {rel}")
    if any(rel == prefix or rel.startswith(prefix + "/") for prefix in PRESERVED_PREFIXES):
        raise FreshStartError(f"Refusing preserved path: {rel}")


def _delete_candidate(candidate: DeleteCandidate) -> bool:
    path = candidate.path
    if not path.exists():
        return False
    if path.is_file():
        path.unlink()
        return True
    if path.is_dir():
        try:
            path.rmdir()
            return True
        except OSError:
            return False
    return False


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _tracked_generated_artifact_rows(tracked: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rel_target in GENERATED_TARGETS:
        prefix = rel_target.strip("/").replace("\\", "/")
        for rel in sorted(tracked):
            if rel == prefix or rel.startswith(prefix + "/"):
                rows.append(
                    {
                        "path": rel,
                        "target": prefix,
                        "status": "REJECTED",
                        "reason": "tracked_generated_artifact_outside_active_run_boundary",
                    }
                )
    return rows


def fresh_start(
    repo: Path,
    scope: str = "all-lanes",
    confirm_hard_delete: bool = False,
    *,
    run_id: str | None = None,
    tracked_paths_fn: Callable[[Path], set[str]] | None = None,
    tracked_checker: Callable[[Path], bool] | None = None,
) -> dict[str, Any]:
    """Delete generated evidence and create an active all-lane run manifest."""

    if scope != "all-lanes":
        raise FreshStartError(f"Unsupported fresh-start scope: {scope}")
    if not confirm_hard_delete:
        raise FreshStartError("Refusing hard delete without confirm_hard_delete=True")

    repo = repo.resolve()
    if tracked_paths_fn is not None and tracked_checker is not None:
        raise FreshStartError("Provide either tracked_paths_fn or tracked_checker, not both")
    tracked = tracked_paths_fn(repo) if tracked_paths_fn else _tracked_paths(repo)
    rejected_stale_rows = _tracked_generated_artifact_rows(tracked)
    delete_candidates: list[DeleteCandidate] = []
    for rel_target in GENERATED_TARGETS:
        target = (repo / rel_target).resolve()
        _validate_target(repo, target)
        delete_candidates.extend(_iter_delete_candidates(repo, target, tracked, tracked_checker) or [])

    created_at = _utc_now()
    run_id = run_id or _default_run_id()
    manifest_dir = repo / "runtime" / "workbench" / "fresh_start_manifests"
    pre_delete_path = manifest_dir / f"{run_id}_pre_delete.json"
    pre_delete = {
        "schema_version": "workbench_fresh_start_pre_delete_v1",
        "run_id": run_id,
        "scope": "all_lanes",
        "created_at_utc": created_at,
        "confirm_hard_delete": True,
        "targets": list(GENERATED_TARGETS),
        "delete_candidates": [
            {
                "path": candidate.relative_path,
                "kind": candidate.kind,
                "bytes": candidate.bytes,
                "mtime": candidate.mtime,
                "reason": "generated_evidence_cleanup",
            }
            for candidate in delete_candidates
        ],
        "preserved_paths": [row["path"] for row in rejected_stale_rows],
        "rejected_stale_artifacts": rejected_stale_rows,
    }
    _write_json(pre_delete_path, pre_delete)

    deleted_paths: list[str] = []
    for candidate in delete_candidates:
        if _delete_candidate(candidate):
            deleted_paths.append(candidate.relative_path)

    active = {
        "schema_version": "workbench_active_run_v1",
        "run_id": run_id,
        "scope": "all_lanes",
        "created_at_utc": created_at,
        "fresh_start": True,
        "artifact_reuse_policy": "active_run_id_only",
        "deleted_paths": deleted_paths,
        "preserved_paths": [row["path"] for row in rejected_stale_rows],
        "rejected_stale_artifacts": rejected_stale_rows,
        "rejected_stale_artifact_count": len(rejected_stale_rows),
        "source_data_reused": True,
        "previous_run_artifacts_reused": False,
        "pre_delete_manifest": str(pre_delete_path),
        "state": "fresh",
    }
    active_path = repo / "runtime" / "workbench" / "active_run.json"
    lock_path = repo / "runtime" / "workbench" / "active_run.lock"
    _write_json(active_path, active)
    lock_path.write_text(
        json.dumps({"run_id": run_id, "created_at_utc": created_at}, indent=2) + "\n",
        encoding="utf-8",
    )

    run_dir = repo / "runtime" / "workbench" / "all_lanes" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        run_dir / "rejected_stale_artifacts.json",
        {
            "schema_version": "rejected_stale_artifacts_v1",
            "run_id": run_id,
            "rows": rejected_stale_rows,
            "rejected_count": len(rejected_stale_rows),
        },
    )

    return {
        "status": "PASS",
        "run_id": run_id,
        "active_run": str(active_path),
        "active_run_lock": str(lock_path),
        "pre_delete_manifest": str(pre_delete_path),
        "deleted_count": len(deleted_paths),
        "deleted_paths": deleted_paths,
    }


def reset_generated_dir(path: Path) -> None:
    """Test helper for generated directories only."""

    if path.exists():
        shutil.rmtree(path)
