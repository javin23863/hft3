#!/usr/bin/env python3
"""HFT3 autonomous research runner launcher.

Thin wrapper that sets up the python path so `hft3` is importable,
then delegates to `hft3.research.run_autonomous.main`.

Two ways to run:
  1. `python hft3-research.py --config configs/research/autonomous_hft3.yaml`
     (works from the repo root without installing the package)
  2. After `pip install -e .`, use the `hft3-research` console script
     or `python -m hft3.research.run_autonomous`.

The spec requires the system to be runnable from CLI; this wrapper
makes that work in a fresh shell without any package install.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_ROOT / "packages"))
sys.path.insert(0, str(_REPO_ROOT / "apps"))

from hft3.research.run_autonomous import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
