#!/usr/bin/env python3
"""Compatibility wrapper for the unified economic event universe builder."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
packages = _REPO / "packages"
if str(packages) not in sys.path:
    sys.path.insert(0, str(packages))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from economic_event_universe.cli import main as event_universe_main


def main(argv: list[str] | None = None) -> int:
    return event_universe_main(["build-events", *(argv if argv is not None else sys.argv[1:])])


if __name__ == "__main__":
    raise SystemExit(main())
