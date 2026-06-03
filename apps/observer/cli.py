from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .read_model import ArtifactLoadError, load_observer_view, render_view


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="observer", description="Read-only Trade Manager observer")
    sub = parser.add_subparsers(dest="command")
    view = sub.add_parser("view", help="Render a read-only session snapshot")
    view.add_argument("--session-id", required=True)
    view.add_argument("--sessions-root", default="artifacts/sessions")

    args = parser.parse_args(argv)
    if args.command != "view":
        parser.print_help()
        return 2

    try:
        snapshot = load_observer_view(Path(args.sessions_root), args.session_id)
    except ArtifactLoadError as exc:
        print(f"observer load error: {exc}", file=sys.stderr)
        return 1

    print(render_view(snapshot))
    return 0
