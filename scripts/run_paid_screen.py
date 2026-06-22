#!/usr/bin/env python3
"""Canonical entry: dispatch to paid-screen orchestrator (long-lived workers)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

_RETIRED_V1_FLAG = "--execution-mode"
_ORCHESTRATOR = _REPO / "scripts" / "run_vectorbt_paid_screen_v2.py"


def _strip_retired_flags(argv: list[str]) -> list[str]:
    """Drop legacy v1/v2 selector; warn if v1 was requested."""
    out: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == _RETIRED_V1_FLAG:
            if i + 1 >= len(argv):
                print(
                    f"ERROR: {_RETIRED_V1_FLAG} requires a value; "
                    "v1 orchestrator retired — use run_paid_screen.py",
                    file=sys.stderr,
                )
                raise SystemExit(2)
            mode = argv[i + 1]
            if mode == "v1":
                print(
                    "ERROR: v1 paid-screen orchestrator retired (2026-06). "
                    "Use scripts/run_paid_screen.py or run_vectorbt_paid_screen.py.",
                    file=sys.stderr,
                )
                raise SystemExit(2)
            if mode != "v2":
                print(
                    f"ERROR: unknown {_RETIRED_V1_FLAG}={mode!r}; "
                    "only v2 is supported",
                    file=sys.stderr,
                )
                raise SystemExit(2)
            print(
                f"WARN: {_RETIRED_V1_FLAG} v2 is ignored; "
                "run_paid_screen.py always uses the paid-screen orchestrator",
                file=sys.stderr,
            )
            i += 2
            continue
        if arg.startswith(f"{_RETIRED_V1_FLAG}="):
            mode = arg.split("=", 1)[1]
            if mode == "v1":
                print(
                    "ERROR: v1 paid-screen orchestrator retired (2026-06). "
                    "Use scripts/run_paid_screen.py or run_vectorbt_paid_screen.py.",
                    file=sys.stderr,
                )
                raise SystemExit(2)
            print(
                f"WARN: {_RETIRED_V1_FLAG}={mode} is ignored; "
                "run_paid_screen.py always uses the paid-screen orchestrator",
                file=sys.stderr,
            )
            i += 1
            continue
        out.append(arg)
        i += 1
    return out


def main(argv: list[str] | None = None) -> int:
    if not _ORCHESTRATOR.is_file():
        print(
            f"ERROR: paid-screen orchestrator missing: {_ORCHESTRATOR}",
            file=sys.stderr,
        )
        return 2
    tail = _strip_retired_flags(list(argv) if argv is not None else sys.argv[1:])
    return subprocess.call([sys.executable, str(_ORCHESTRATOR)] + tail)


if __name__ == "__main__":
    raise SystemExit(main())
