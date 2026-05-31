"""Rule-based KG queries for similar workbench runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _load_nodes(repo_root: Path) -> List[Dict[str, Any]]:
    path = repo_root / "research_cards" / "kg" / "nodes.jsonl"
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def find_similar_runs(
    repo_root: Path,
    model_id: str,
    event_context: str,
    latency_lane: str,
    *,
    breakeven_us: Optional[float] = None,
    exclude_run_id: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Match on event_context + lane + similar breakeven band (±25%)."""
    candidates = [
        n for n in _load_nodes(repo_root)
        if n.get("type") == "backtest-run"
        and n.get("model_id") == model_id
        and n.get("event_context") == event_context
        and n.get("latency_lane") == latency_lane
        and (exclude_run_id is None or n.get("run_id") != exclude_run_id)
    ]
    if breakeven_us is not None and breakeven_us > 0:
        lo, hi = breakeven_us * 0.75, breakeven_us * 1.25

        def _in_band(n: Dict[str, Any]) -> bool:
            be = n.get("breakeven_us")
            return isinstance(be, (int, float)) and lo <= float(be) <= hi

        band = [n for n in candidates if _in_band(n)]
        if band:
            candidates = band
    return [
        {
            "run_id": n.get("run_id"),
            "event_context": n.get("event_context"),
            "latency_lane": n.get("latency_lane"),
            "breakeven_us": n.get("breakeven_us"),
            "lane_pass": n.get("lane_pass"),
        }
        for n in candidates[:limit]
    ]
