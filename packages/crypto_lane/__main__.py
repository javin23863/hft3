"""CLI entry: ``python -m crypto_lane <subcommand>`` (requires repo root on PYTHONPATH or pip install -e .)."""

from crypto_lane.pipeline import main

if __name__ == "__main__":
    raise SystemExit(main())
