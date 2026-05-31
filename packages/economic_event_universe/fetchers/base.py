"""Write propose-only calendar diff artifacts."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from hft3_bootstrap import repo_root


def proposals_dir(repo: Path | None = None) -> Path:
    root = repo or repo_root()
    out = root / "artifacts" / "calendar_proposals"
    out.mkdir(parents=True, exist_ok=True)
    return out


def write_proposal(agency: str, rows: list[dict[str, Any]], *, repo: Path | None = None) -> Path:
    out_dir = proposals_dir(repo)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    path = out_dir / f"{agency}_{stamp}.json"
    payload = {
        "agency": agency,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "policy": "propose_only; merge manually into release_calendars/",
        "rows": rows,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
