#!/usr/bin/env python3
"""Generate HftBacktest latency_model regime JSON artifacts from latency_summary.json."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import sys

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO / "packages") not in sys.path:
    sys.path.insert(0, str(_REPO / "packages"))

from backtest_pipeline.src.chi404_latency import (  # noqa: E402
    build_latency_model_from_summary,
    enrich_latency_model_probe_evidence,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=_REPO)
    parser.add_argument("--regime", action="append", default=["fast", "normal", "stress", "extreme"])
    args = parser.parse_args()
    repo = args.repo.resolve()
    summary_path = repo / "runtime" / "latency_reports" / "latency_summary.json"
    out_dir = repo / "reports" / "latency_baselines" / "live_r01_chicago"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not summary_path.is_file():
        raise SystemExit(f"latency_summary.json missing: {summary_path}")

    summary: dict[str, Any] = json.loads(summary_path.read_text(encoding="utf-8"))

    written: list[str] = []
    for regime in args.regime:
        model = build_latency_model_from_summary(regime=regime, summary=summary)
        model = enrich_latency_model_probe_evidence(model, chi404_summary=summary_path)
        out_path = out_dir / f"latency_model_{regime}.json"
        out_path.write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
        written.append(str(out_path.relative_to(repo)).replace("\\", "/"))

    print(json.dumps({"written": written}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
