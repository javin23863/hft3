"""Read/write normalized session NDJSON."""
from __future__ import annotations

import json
from pathlib import Path

from equities_lane.src.models import SessionTick
from equities_lane.src.types import SessionMeta


def load_session(path: str | Path) -> tuple[SessionMeta, list[SessionTick]]:
    p = Path(path)
    meta: SessionMeta | None = None
    ticks: list[SessionTick] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("_type") == "meta":
            from equities_lane.src.types import DegradedModeFlags

            deg = row.get("degraded_assumptions", [])
            meta = SessionMeta(
                symbol=row["symbol"],
                session_date=row["session_date"],
                prior_close=float(row["prior_close"]),
                premarket_open=float(row["premarket_open"]),
                degraded=DegradedModeFlags(
                    degraded_mode=bool(row.get("degraded_mode", False)),
                    assumptions=list(deg),
                ),
            )
        else:
            ticks.append(SessionTick.from_dict(row))
    if meta is None:
        raise ValueError(f"Session file missing meta header: {p}")
    return meta, ticks


def save_session(path: str | Path, meta: SessionMeta, ticks: list[SessionTick]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"_type": "meta", **meta.to_dict()})]
    lines.extend(json.dumps(t.to_dict()) for t in ticks)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p
