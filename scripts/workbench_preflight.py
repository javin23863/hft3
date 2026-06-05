#!/usr/bin/env python3
"""Preflight workbench imports for scripts/launch_workbench.ps1."""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def _repo_root() -> Path:
    """Resolve repo root from this script's location (launcher sets PYTHONPATH)."""
    script_repo = Path(__file__).resolve().parents[1]
    if (script_repo / "apps" / "workbench").is_dir():
        return script_repo
    for part in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        candidate = Path(part).resolve()
        if (candidate / "apps" / "workbench").is_dir():
            return candidate
    return script_repo


def _bootstrap_sys_path(repo: Path) -> None:
    repo_str = str(repo)
    apps_str = str(repo / "apps")
    pkg_str = str(repo / "packages")
    for p in (repo_str, pkg_str, apps_str):
        if p not in sys.path:
            sys.path.insert(0, p)
    os.environ.setdefault("PYTHONPATH", f"{repo_str}{os.pathsep}{pkg_str}{os.pathsep}{apps_str}")


def _assert_catalog_keys_namespaced(repo: Path) -> None:
    """Fail fast when campaign_panel still uses global catalog_search keys."""
    panel_path = repo / "apps" / "workbench" / "ui" / "campaign_panel.py"
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
            "update workbench/ui/campaign_panel.py from main"
        )


def main() -> int:
    repo = _repo_root()
    _bootstrap_sys_path(repo)

    try:
        _assert_catalog_keys_namespaced(repo)
        from workbench.src.core.composition import CatalogEntry, DefensiveStub, ModelComposition
        from workbench.src.registry.model_catalog import load_catalog
        from workbench.src.runtime_contract import validate_runtime_contract
        from workbench.ui.analyst_panel import workbench_llm_console
        from workbench.ui.campaign_panel import get_session_composition
        from workbench.ui.workflow_tabs import WORKFLOW_TABS

        catalog = load_catalog(repo)
        if not catalog:
            raise RuntimeError(f"load_catalog() returned empty catalog (repo={repo})")
        contract_errors = validate_runtime_contract()
        if contract_errors:
            raise RuntimeError("workbench runtime contract invalid: " + "; ".join(contract_errors))
        if "Model Selector" in WORKFLOW_TABS:
            raise RuntimeError("workbench tabs are stale: WORKFLOW_TABS still contains 'Model Selector'")
        if "Registry & Data" not in WORKFLOW_TABS:
            raise RuntimeError("workbench tabs are stale: WORKFLOW_TABS missing 'Registry & Data'")

        _ = CatalogEntry, DefensiveStub, ModelComposition, get_session_composition, workbench_llm_console
    except Exception:
        print(f"workbench preflight failed (repo={repo})", file=sys.stderr)
        traceback.print_exc()
        return 1

    print("workbench import OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
