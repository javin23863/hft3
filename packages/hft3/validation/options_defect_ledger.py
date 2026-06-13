"""Options-lane known-defect ledger reader.

The authoritative ledger lives in specs/OPTIONS_LANE.md. This module keeps
cockpit and promotion gates tied to that ontology rather than duplicating the
open item list in code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OptionsDefectLedger:
    status: str
    open_count: int
    open_ids: tuple[str, ...]
    items: tuple[dict[str, object], ...]
    artifact: str
    reason: str = ""

    @property
    def empty(self) -> bool:
        return self.status == "empty" and self.open_count == 0

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "empty": self.empty,
            "open_count": self.open_count,
            "open_ids": list(self.open_ids),
            "items": list(self.items),
            "artifact": self.artifact,
            "reason": self.reason,
        }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


_CLOSED_STATUS_TOKENS = frozenset({"FIXED", "WAIVED"})
_KNOWN_STATUS_TOKENS = frozenset({"OPEN", *_CLOSED_STATUS_TOKENS})


def _status_token(status: str) -> str:
    match = re.search(r"[A-Z][A-Z_]*", status.replace("*", "").upper())
    return match.group(0) if match else ""


def load_options_defect_ledger(root: Path | None = None) -> OptionsDefectLedger:
    repo = Path(root) if root is not None else _repo_root()
    path = repo / "specs" / "OPTIONS_LANE.md"
    artifact = "specs/OPTIONS_LANE.md"
    if not path.is_file():
        return OptionsDefectLedger(
            status="missing",
            open_count=1,
            open_ids=("OPTIONS_LEDGER_MISSING",),
            items=(),
            artifact=artifact,
            reason="options ledger missing; fail closed",
        )

    items: list[dict[str, object]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("| o-"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 4:
            item_id = cells[0] if cells else "OPTIONS_LEDGER_MALFORMED_ROW"
            items.append(
                {
                    "id": item_id,
                    "component": cells[1] if len(cells) > 1 else "",
                    "description": cells[2] if len(cells) > 2 else "",
                    "status": "",
                    "status_token": "MALFORMED",
                    "is_open": True,
                    "is_malformed": True,
                    "unknown_status": False,
                }
            )
            continue
        item_id = cells[0]
        component = cells[1]
        description = " | ".join(cells[2:-1])
        status = cells[-1]
        token = _status_token(status)
        unknown_status = token not in _KNOWN_STATUS_TOKENS
        is_open = token == "OPEN" or unknown_status
        items.append(
            {
                "id": item_id,
                "component": component,
                "description": description,
                "status": status,
                "status_token": token or "UNKNOWN",
                "is_open": is_open,
                "is_malformed": False,
                "unknown_status": unknown_status,
            }
        )

    if not items:
        return OptionsDefectLedger(
            status="unparseable",
            open_count=1,
            open_ids=("OPTIONS_LEDGER_UNPARSEABLE",),
            items=(),
            artifact=artifact,
            reason="options ledger had no parseable o-* rows; fail closed",
        )

    open_items = tuple(item for item in items if item["is_open"] is True)
    if open_items:
        if any(item.get("is_malformed") for item in open_items):
            status = "malformed"
            reason = "options ledger has malformed o-* rows; fail closed"
        elif any(item.get("unknown_status") for item in open_items):
            status = "unknown_status"
            reason = "options ledger has unknown o-* statuses; fail closed"
        else:
            status = "blocked"
            reason = ""
    else:
        status = "empty"
        reason = "options ledger empty"
    return OptionsDefectLedger(
        status=status,
        open_count=len(open_items),
        open_ids=tuple(item["id"] for item in open_items),
        items=tuple(items),
        artifact=artifact,
        reason=reason,
    )
