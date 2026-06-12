"""Entry point: python -m llm_slow_tier <subcommand> ..."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
