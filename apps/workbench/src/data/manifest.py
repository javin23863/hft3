"""Dataset manifest and history sufficiency gate."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from workbench.src.data.l3_loader import LoaderReport


@dataclass
class DatasetManifest:
    npz_path: str
    event_id: str
    event_count: int
    gap_count: int
    duplicate_order_ids: int
    monotonic_violations: int
    ptp_note: str = "CHI404 probe metadata; PTP assumed on colo path"
    min_history_years_required: int = 10
    history_years_available: float = 0.0
    data_sufficient: bool = False
    chi404_run_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_loader(
        cls,
        npz_path: Path,
        event_id: str,
        report: LoaderReport,
        *,
        min_history_years: int = 10,
        history_years_available: float = 0.0,
        chi404_summary: Optional[Dict[str, Any]] = None,
    ) -> "DatasetManifest":
        sufficient = history_years_available >= min_history_years
        run_id = None
        if chi404_summary:
            run_id = chi404_summary.get("run_id")
        return cls(
            npz_path=str(npz_path),
            event_id=event_id,
            event_count=report.event_count,
            gap_count=report.gap_count,
            duplicate_order_ids=report.duplicate_order_ids,
            monotonic_violations=report.monotonic_violations,
            min_history_years_required=min_history_years,
            history_years_available=history_years_available,
            data_sufficient=sufficient,
            chi404_run_id=run_id,
        )

    def gate_error(self) -> Optional[str]:
        if not self.data_sufficient:
            return (
                f"DATA_INSUFFICIENT: need {self.min_history_years_required}y history, "
                f"have {self.history_years_available}y"
            )
        return None

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
