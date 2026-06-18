#!/usr/bin/env python3
"""Merge CC-2/3/4 campaign summaries into runtime/latency_reports/latency_truth.json."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO / "packages") not in sys.path:
    sys.path.insert(0, str(_REPO / "packages"))

from backtest_pipeline.src.latency_components import (  # noqa: E402
    default_component_bands,
    merge_component_bands_from_cc_summaries,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ingest_latency_truth_components(
    *,
    repo: Path,
    truth_path: Path | None = None,
) -> dict[str, Any]:
    repo = repo.resolve()
    truth_path = (truth_path or repo / "runtime" / "latency_reports" / "latency_truth.json").resolve()
    if not truth_path.is_file():
        raise FileNotFoundError(f"latency_truth.json missing: {truth_path}")

    truth: dict[str, Any] = json.loads(truth_path.read_text(encoding="utf-8"))
    live_placement = truth.get("live_placement") if isinstance(truth.get("live_placement"), dict) else None
    bands = default_component_bands(live_placement=live_placement)
    bands = merge_component_bands_from_cc_summaries(repo, bands)

    truth["component_bands"] = bands
    truth["generated_utc"] = _utc_now()
    cc_meta = truth.setdefault("cc_component_ingest", {})
    if isinstance(cc_meta, dict):
        cc_meta["last_ingest_utc"] = truth["generated_utc"]
        cc_meta["script"] = "scripts/latency_probe/ingest_latency_truth_components.py"
        cc_meta["repo"] = str(repo)

    truth_path.parent.mkdir(parents=True, exist_ok=True)
    truth_path.write_text(json.dumps(truth, indent=2) + "\n", encoding="utf-8")
    return truth


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=_REPO)
    parser.add_argument(
        "--truth",
        type=Path,
        default=None,
        help="Override path to latency_truth.json",
    )
    args = parser.parse_args()

    truth = ingest_latency_truth_components(repo=args.repo, truth_path=args.truth)
    bands = truth.get("component_bands") or {}
    summary = {
        name: (bands.get(name) or {}).get("measurement_status")
        for name in (
            "feed_latency_us",
            "new_send_to_exchange_us",
            "new_exchange_to_ack_us",
            "cancel_send_to_exchange_us",
            "cancel_exchange_to_ack_us",
        )
    }
    print(json.dumps({"written": "runtime/latency_reports/latency_truth.json", "component_bands": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
