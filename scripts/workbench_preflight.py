#!/usr/bin/env python3
"""Preflight workbench imports for scripts/launch_workbench.ps1."""
from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _repo_root() -> Path:
    """Resolve repo root from this script's location."""
    return Path(__file__).resolve().parents[1]


def _assert_catalog_keys_namespaced(repo: Path) -> None:
    """Fail fast when campaign_panel still uses global catalog_search keys."""
    panel_path = repo / "apps" / "workbench" / "ui" / "campaign_panel.py"
    if not panel_path.is_file():
        raise RuntimeError(f"missing {panel_path} (repo={repo})")
    text = panel_path.read_text(encoding="utf-8")
    forbidden = (
        'key="catalog_search"',
        "key='catalog_search'",
        'key_prefix = "catalog"',
        "key_prefix = 'catalog'",
    )
    for pattern in forbidden:
        if pattern in text:
            raise RuntimeError(
                f"{panel_path} still contains {pattern!r}; "
                "git pull and ensure key_prefix uses alpha_catalog/hybrid_catalog/defensive_catalog"
            )

    from workbench.ui import campaign_panel

    if not hasattr(campaign_panel, "_catalog_widget_key"):
        raise RuntimeError(
            "campaign_panel missing _catalog_widget_key(); "
            "update apps/workbench/ui/campaign_panel.py from main"
        )


def main() -> int:
    repo = _repo_root()

    # Make repo root importable so hft3_bootstrap is reachable regardless of
    # whether the launcher (which sets PYTHONPATH) or a bare python invocation
    # is used. Idempotent and harmless if already on sys.path.
    repo_str = str(repo)
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)

    # hft3_bootstrap wires packages/ and apps/ into sys.path
    try:
        import hft3_bootstrap
        hft3_bootstrap.setup_repo_paths()
    except Exception as exc:
        print(f"hft3_bootstrap.setup_repo_paths() failed: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1

    try:
        _assert_catalog_keys_namespaced(repo)
        from workbench.src.core.composition import CatalogEntry, DefensiveStub, ModelComposition
        from workbench.src.registry.model_catalog import load_catalog
        from workbench.ui.campaign_panel import get_session_composition

        catalog = load_catalog(repo)
        if not catalog:
            raise RuntimeError(f"load_catalog() returned empty catalog (repo={repo})")

        _ = CatalogEntry, DefensiveStub, ModelComposition, get_session_composition
    except Exception:
        print(f"workbench preflight failed (repo={repo})", file=sys.stderr)
        traceback.print_exc()
        return 1

    print("workbench import OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
