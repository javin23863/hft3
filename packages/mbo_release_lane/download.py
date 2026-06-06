"""Download orchestrator for MBO-only release lane."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mbo_release_lane.constants import DEFAULT_DATASET_ID, MBO_SCHEMA, SOURCE_VENDOR
from mbo_release_lane.import_pipeline import ImportResult, import_release_window
from mbo_release_lane.storage import release_slot_dir

logger = logging.getLogger(__name__)


@dataclass
class DownloadReport:
    release_count: int = 0
    symbol_count: int = 0
    window_start_offset: str = "-60s"
    window_end_offset: str = "+10s"
    total_events: int = 0
    dataset_ids: list[str] = field(default_factory=lambda: [DEFAULT_DATASET_ID])
    source_vendor: str = SOURCE_VENDOR
    products: list[str] = field(default_factory=list)
    missing_windows: list[dict[str, str]] = field(default_factory=list)
    rejected_files: list[dict[str, str]] = field(default_factory=list)
    sequence_gap_count: int = 0
    blocker_count: int = 0
    valid_release_paths: list[str] = field(default_factory=list)
    invalid_release_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mbo_download_report": {
                "release_count": self.release_count,
                "symbol_count": self.symbol_count,
                "window_start_offset": self.window_start_offset,
                "window_end_offset": self.window_end_offset,
                "total_events": self.total_events,
                "dataset_ids": self.dataset_ids,
                "source_vendor": self.source_vendor,
                "products": sorted(set(self.products)),
                "missing_windows": self.missing_windows,
                "rejected_files": self.rejected_files,
                "sequence_gap_count": self.sequence_gap_count,
                "blocker_count": self.blocker_count,
                "valid_release_paths": self.valid_release_paths,
                "invalid_release_paths": self.invalid_release_paths,
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        }


def _as_datetime(value: Any):
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def _event_spec(repo_root: Path, window, symbol: str):
    from workbench.src.data.event_catalog import EventSpec
    from economic_event_universe.registry import default_cme_symbols

    return EventSpec(
        event_id=window.event_id,
        event_type=window.event_type,
        release_date=window.release_date,
        event_context=window.window_name,
        symbol=symbol,
        npz_path=Path(),
        npz_present=False,
        start_utc=window.start_utc,
        end_utc=window.end_utc,
        parsed_symbols=default_cme_symbols(),
    )


def download_catalog_slot(
    repo_root: Path,
    window,
    symbol: str,
    *,
    max_cost_usd: float | None = None,
    skip_if_valid: bool = True,
) -> ImportResult | None:
    """Download one release window for one symbol through MBO-only lane."""
    from workbench.src.data.catalog_backfill import resolve_download_symbol
    from data_system.src.databento_client import DatabentoResearchClient

    slot = release_slot_dir(repo_root, window.event_id, symbol)
    manifest = slot / "release_event_path.json"
    if skip_if_valid and manifest.is_file():
        import json

        data = json.loads(manifest.read_text(encoding="utf-8"))
        rep = data.get("release_event_path", {})
        if rep.get("validation_status") == "valid":
            return ImportResult(
                release_id=window.event_id,
                symbol=symbol,
                slot_dir=slot,
                validation_status="valid",
                event_count=int(rep.get("event_count", 0)),
                blockers=[],
                paths_written=[str(manifest)],
            )

    try:
        client = DatabentoResearchClient()
    except ValueError as exc:
        logger.error("No DATABENTO_API_KEY: %s", exc)
        return None

    ev = _event_spec(repo_root, window, symbol)
    start = _as_datetime(window.start_utc)
    end = _as_datetime(window.end_utc)

    if max_cost_usd is not None:
        sym_used, cost = resolve_download_symbol(client, ev)
        if cost > max_cost_usd:
            logger.warning("Cost %.4f exceeds max for %s", cost, window.event_id)
            return None
    else:
        sym_used, _ = resolve_download_symbol(client, ev)

    raw_dest = slot / "raw.dbn.zst"
    slot.mkdir(parents=True, exist_ok=True)
    dbn_path = client.download_event_window(
        event_id=window.event_id,
        symbols=[sym_used],
        start_utc=start,
        end_utc=end,
        schema=MBO_SCHEMA,
        requested_symbol=symbol,
        output_path=str(raw_dest),
    )

    anchor = _as_datetime(window.start_utc)
    # scheduled anchor = end of pre-window offset
    from economic_event_universe.windows import download_window

    start_off, _ = download_window(window.event_type)
    scheduled = anchor  # window.start_utc is already anchor + start_off

    return import_release_window(
        repo_root,
        release_id=window.event_id,
        release_name=window.event_type,
        symbol=symbol,
        raw_dbn_src=Path(dbn_path),
        window_start=window.start_utc.isoformat() if hasattr(window.start_utc, "isoformat") else str(window.start_utc),
        window_end=window.end_utc.isoformat() if hasattr(window.end_utc, "isoformat") else str(window.end_utc),
        scheduled_release_timestamp=scheduled.isoformat(),
        dataset_id=DEFAULT_DATASET_ID,
    )


def run_catalog_download(
    repo_root: Path,
    *,
    include_seed: bool = True,
    include_rule_based: bool = False,
    start_year: int = 2018,
    end_year: int = 2025,
    symbols: tuple[str, ...] | None = None,
    max_cost_usd: float | None = None,
    limit: int | None = None,
) -> DownloadReport:
    from economic_event_universe.registry import default_cme_symbols
    from economic_event_universe.window_catalog import iter_catalog_windows

    syms = symbols or default_cme_symbols()
    windows = iter_catalog_windows(
        repo_root,
        include_seed=include_seed,
        include_rule_based=include_rule_based,
        start_year=start_year,
        end_year=end_year,
    )

    report = DownloadReport(symbol_count=len(syms), products=list(syms))
    slot_count = 0

    for window in windows:
        if limit is not None and slot_count >= limit:
            break
        report.release_count += 1
        for symbol in syms:
            if limit is not None and slot_count >= limit:
                break
            try:
                result = download_catalog_slot(
                    repo_root,
                    window,
                    symbol,
                    max_cost_usd=max_cost_usd,
                )
            except Exception as exc:
                logger.exception("Failed %s %s: %s", window.event_id, symbol, exc)
                report.rejected_files.append(
                    {"release_id": window.event_id, "symbol": symbol, "reason": str(exc)}
                )
                report.blocker_count += 1
                continue

            if result is None:
                report.missing_windows.append({"release_id": window.event_id, "symbol": symbol})
                continue

            slot_count += 1
            report.total_events += result.event_count
            rel_path = str(result.slot_dir.relative_to(repo_root))
            if result.validation_status == "valid":
                report.valid_release_paths.append(rel_path)
            else:
                report.invalid_release_paths.append(rel_path)
                report.blocker_count += len(result.blockers)

    return report
