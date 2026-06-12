"""Topology guard for the Rithmic MBO fill source.

Mirrors ``data_system.rithmic_trial.platform.is_windows()`` refusal pattern
(BLUEPRINT §4, AGENTS.md).  Rithmic historical data fetches must run on
CHI404 bare metal — never on a Windows dev workstation.
"""

from __future__ import annotations

import sys


def is_windows() -> bool:
    return sys.platform == "win32"


class RithmicTopologyError(RuntimeError):
    """Raised when a Rithmic fill-source operation violates the topology rule."""


def assert_rithmic_topology_ok() -> None:
    """Raise RithmicTopologyError when called on Windows.

    Use as the first call inside any MBO release lane entry point that
    will reach out to the Rithmic API.  Mirrors the trial lane's
    pipeline.is_windows() refusal.
    """
    if is_windows():
        raise RithmicTopologyError(
            "Rithmic MBO fill source must run on CHI404 bare metal, "
            "not the dev workstation (BLUEPRINT §4)."
        )
