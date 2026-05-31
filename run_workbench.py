#!/usr/bin/env python3
"""Launcher: python run_workbench.py run --model SPREAD_BLOWOUT_RECOMPRESSION ..."""
from __future__ import annotations

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from workbench.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
