#!/usr/bin/env python3
"""Dispatch shim: select v1 or v2 paid-screen orchestrator via --execution-mode."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--execution-mode",
        choices=["v1", "v2"],
        default="v2",
        help="v2=long-lived workers (default); v1=subprocess-per-unit rollback",
    )
    known, rest = parser.parse_known_args(argv)
    script = (
        "run_vectorbt_paid_screen.py"
        if known.execution_mode == "v1"
        else "run_vectorbt_paid_screen_v2.py"
    )
    return subprocess.call([sys.executable, str(_REPO / "scripts" / script)] + rest)


if __name__ == "__main__":
    raise SystemExit(main())
