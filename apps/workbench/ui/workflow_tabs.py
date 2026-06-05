"""Workflow tab order from the runtime contract."""

from typing import Any

from workbench.src.runtime_contract import load_runtime_contract

WORKFLOW_TAB_CONTRACTS: list[dict[str, Any]] = load_runtime_contract()["tabs"]
WORKFLOW_TABS = [str(tab["name"]) for tab in WORKFLOW_TAB_CONTRACTS]
