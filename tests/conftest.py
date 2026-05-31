"""Pytest path setup."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

# Canonical artifact root for tests (nested layout after migration).
_nested = ROOT / "artifacts" / "research_cards"
if _nested.is_dir() and "HFT3_ARTIFACTS_ROOT" not in os.environ:
    os.environ["HFT3_ARTIFACTS_ROOT"] = str(_nested)
