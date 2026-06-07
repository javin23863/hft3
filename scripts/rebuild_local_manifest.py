#!/usr/bin/env python3
"""Rebuild local data/manifest.parquet from existing mbo_release raw slots."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()


def main() -> int:
    from data_system.src.manifest_io import rebuild_from_mbo_release_slots

    out = rebuild_from_mbo_release_slots(_REPO)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
