"""Load parity universe config and enforce lane quarantine."""
from __future__ import annotations

from pathlib import Path

import yaml

from options_lane.src.models import ParityGroup


def _assert_quarantine(path: Path, repo_root: Path, label: str) -> None:
    prod_npz = (repo_root / "data" / "npz").resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(prod_npz)
        raise ValueError(
            f"Quarantine violation: {label}={path} must not be under production {prod_npz}"
        )
    except ValueError as exc:
        if "Quarantine violation" in str(exc):
            raise
        return


def load_universe(path: str | Path) -> tuple[Path, list[ParityGroup], dict[str, Path]]:
    cfg_path = Path(path)
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    repo_root = Path(data.get("repo_root", ".")).resolve()
    if not repo_root.is_absolute():
        repo_root = (cfg_path.parent.parent.parent / repo_root).resolve()

    paths_cfg = data.get("paths", {})
    paths = {
        "raw_root": repo_root / paths_cfg.get("raw_root", "data/options/raw"),
        "normalized_root": repo_root / paths_cfg.get("normalized_root", "data/options/normalized"),
        "replay_root": repo_root / paths_cfg.get("replay_root", "data/replay/parity"),
        "reports_root": repo_root / paths_cfg.get("reports_root", "research_cards/parity"),
    }
    for label, p in paths.items():
        _assert_quarantine(p, repo_root, label)

    groups = [ParityGroup.from_dict(g) for g in data.get("parity_groups", [])]
    return repo_root, groups, paths


def load_group_by_id(path: str | Path, group_id: str) -> ParityGroup:
    _, groups, _ = load_universe(path)
    for g in groups:
        if g.id == group_id:
            return g
    raise KeyError(f"parity group {group_id!r} not found in {path}")
