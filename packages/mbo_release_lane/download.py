"""Download orchestrator for MBO-only release lane."""

from __future__ import annotations

import logging
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mbo_release_lane.constants import (
    DEFAULT_DATASET_ID,
    DEFAULT_DOWNLOAD_EXCLUDE_EVENT_TYPES,
    MBO_SCHEMA,
    SOURCE_VENDOR,
)
from mbo_release_lane.import_pipeline import ImportResult, import_release_window
from mbo_release_lane.storage import release_slot_dir

logger = logging.getLogger(__name__)

_tls = threading.local()
_report_lock = threading.Lock()


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


def _get_databento_client():
    from data_system.src.databento_client import DatabentoResearchClient

    client = getattr(_tls, "databento_client", None)
    if client is None:
        client = DatabentoResearchClient()
        _tls.databento_client = client
    return client


def download_catalog_slot(
    repo_root: Path,
    window,
    symbol: str,
    *,
    max_cost_usd: float | None = None,
    skip_if_valid: bool = True,
    skip_if_npz: bool = True,
    client: Any | None = None,
    source: str | None = None,
) -> ImportResult | None:
    """Download one release window for one symbol through MBO-only lane.

    ``source`` may be ``"rithmic_api"`` / ``"databento"`` / ``None``.
    ``None`` means "use the priority chain" — Rithmic first, Databento
    fallback (see ``mbo_release_lane.source_priority``).  When Rithmic
    is not applicable (no creds / Windows / non-MBO schema), control
    transparently falls through to Databento.
    """
    from workbench.src.data.catalog_backfill import resolve_download_symbol
    from data_system.src.npz_resolver import resolve_npz_for_event
    from economic_event_universe.registry import default_cme_symbols
    from mbo_release_lane.constants import SOURCE_VENDOR_RITHMIC
    from mbo_release_lane.source_priority import attempt_rithmic_fill, resolve_source

    if skip_if_npz:
        syms = default_cme_symbols()
        _, present, _ = resolve_npz_for_event(repo_root, window.event_id, symbol, syms)
        if present:
            return ImportResult(
                release_id=window.event_id,
                symbol=symbol,
                slot_dir=release_slot_dir(repo_root, window.event_id, symbol),
                validation_status="valid",
                event_count=0,
                blockers=[],
                paths_written=[],
            )

    slot = release_slot_dir(repo_root, window.event_id, symbol)
    manifest = slot / "release_event_path.json"
    raw_dest = slot / "raw.dbn.zst"
    if raw_dest.is_file() and raw_dest.stat().st_size == 0:
        raw_dest.unlink(missing_ok=True)
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
        # Already imported (even if sequence-gap invalid) — do not re-bill Databento.
        if raw_dest.is_file() and raw_dest.stat().st_size > 0 and int(rep.get("event_count", 0)) > 0:
            return ImportResult(
                release_id=window.event_id,
                symbol=symbol,
                slot_dir=slot,
                validation_status=str(rep.get("validation_status", "invalid")),
                event_count=int(rep.get("event_count", 0)),
                blockers=[],
                paths_written=[str(manifest)],
            )
        # Empty/stale import — delete artifacts so Databento fetch can retry.
        raw_dest.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        for stale in (
            slot / "validation.json",
            slot / "events.jsonl",
            slot / "hashes.json",
        ):
            if stale.is_file():
                stale.unlink(missing_ok=True)
    elif raw_dest.is_file():
        # Orphaned/corrupt raw without a valid manifest — force re-fetch.
        raw_dest.unlink(missing_ok=True)

    # Source priority: Rithmic first when the slot is empty and the
    # caller did not pin a specific source.  Rithmic returns events.jsonl
    # + a release_event_path.json with source_vendor="rithmic_api" — that
    # is the same slot layout the Databento import path writes.  If
    # Rithmic raises (topology, missing creds, no entitlement, non-MBO
    # schema, parse failure), control transparently falls through to the
    # existing Databento path below.
    if source != "databento":
        chosen = source or resolve_source(repo_root, window.event_id, symbol)
        if chosen == SOURCE_VENDOR_RITHMIC:
            anchor_dt = _as_datetime(window.start_utc)
            scheduled = anchor_dt  # window.start_utc is already anchor + start_off
            status, manifest, err = attempt_rithmic_fill(
                repo_root,
                release_id=window.event_id,
                symbol=symbol,
                exchange=window.exchange if hasattr(window, "exchange") else "CME",
                start_utc=window.start_utc,
                end_utc=window.end_utc,
                scheduled_release_timestamp=scheduled.isoformat(),
            )
            if status.is_filled() and manifest is not None:
                return ImportResult(
                    release_id=window.event_id,
                    symbol=symbol,
                    slot_dir=release_slot_dir(repo_root, window.event_id, symbol),
                    validation_status="valid",
                    event_count=int(manifest.get("release_event_path", {}).get("event_count", 0)),
                    blockers=[],
                    paths_written=[
                        str(release_slot_dir(repo_root, window.event_id, symbol) / "events.jsonl"),
                        str(release_slot_dir(repo_root, window.event_id, symbol) / "release_event_path.json"),
                    ],
                )
            # Rithmic not applicable — fall through to Databento.
            logger.info(
                "Rithmic source skipped for %s %s: %s — falling back to Databento",
                window.event_id,
                symbol,
                err or "no detail",
            )

    try:
        client = client or _get_databento_client()
    except ValueError as exc:
        logger.error("No DATABENTO_API_KEY: %s", exc)
        return None

    ev = _event_spec(repo_root, window, symbol)
    start = _as_datetime(window.start_utc)
    end = _as_datetime(window.end_utc)

    sym_used, cost = resolve_download_symbol(client, ev)
    if max_cost_usd is not None and cost > max_cost_usd:
        logger.warning("Cost %.4f exceeds max for %s", cost, window.event_id)
        return None

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
        cost_estimate=cost,
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


def _apply_slot_result(
    report: DownloadReport,
    repo_root: Path,
    window,
    symbol: str,
    result: ImportResult | None,
    *,
    exc: Exception | None = None,
) -> None:
    if exc is not None:
        logger.exception("Failed %s %s: %s", window.event_id, symbol, exc)
        report.rejected_files.append(
            {"release_id": window.event_id, "symbol": symbol, "reason": str(exc)}
        )
        report.blocker_count += 1
        return
    if result is None:
        report.missing_windows.append({"release_id": window.event_id, "symbol": symbol})
        return
    report.total_events += result.event_count
    # Slot dirs live in the external lake when HFT3_NPZ_ROOT is set —
    # fall back to the absolute path when not under the repo clone.
    try:
        rel_path = str(result.slot_dir.relative_to(repo_root))
    except ValueError:
        rel_path = str(result.slot_dir)
    if result.validation_status in ("valid", "invalid") and result.event_count > 0:
        report.valid_release_paths.append(rel_path)
        try:
            from mbo_release_lane.npz_adapter import derive_npz_from_release

            derive_npz_from_release(repo_root, window.event_id, symbol)
        except Exception as exc:
            logger.warning("NPZ derive failed %s %s: %s", window.event_id, symbol, exc)
    else:
        report.invalid_release_paths.append(rel_path)
        report.blocker_count += len(result.blockers)


def _shard_key(event_id: str, symbol: str) -> int:
    return zlib.crc32(f"{event_id}:{symbol}".encode("utf-8")) & 0xFFFFFFFF


def resolve_download_exclusions(
    *,
    exclude_event_types: tuple[str, ...] | None = None,
    include_event_types: tuple[str, ...] | None = None,
) -> frozenset[str]:
    """Default download exclusions minus explicit --include-event-type overrides."""
    excluded = set(exclude_event_types or DEFAULT_DOWNLOAD_EXCLUDE_EVENT_TYPES)
    excluded -= set(include_event_types or ())
    return frozenset(excluded)


def filter_windows_by_event_type(
    windows: list,
    *,
    exclude_event_types: frozenset[str] | None = None,
    only_event_types: frozenset[str] | None = None,
) -> list:
    if only_event_types:
        return [w for w in windows if w.event_type in only_event_types]
    if not exclude_event_types:
        return windows
    return [w for w in windows if w.event_type not in exclude_event_types]


def filter_tasks_for_shard(
    tasks: list[tuple[Any, str]],
    *,
    shard_index: int = 0,
    shard_count: int = 1,
) -> list[tuple[Any, str]]:
    if shard_count <= 1:
        return tasks
    idx = int(shard_index) % int(shard_count)
    count = int(shard_count)
    return [
        (window, symbol)
        for window, symbol in tasks
        if _shard_key(window.event_id, symbol) % count == idx
    ]


def _download_slot_task(
    repo_root: Path,
    window,
    symbol: str,
    max_cost_usd: float | None,
    *,
    skip_if_npz: bool = True,
    max_attempts: int = 6,
    source: str | None = None,
) -> tuple[Any, str, ImportResult | None, Exception | None]:
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            result = download_catalog_slot(
                repo_root,
                window,
                symbol,
                max_cost_usd=max_cost_usd,
                skip_if_npz=skip_if_npz,
                source=source,
            )
            return window, symbol, result, None
        except Exception as exc:
            last_exc = exc
            msg = str(exc)
            retryable = (
                "429 Too Many Requests" in msg
                or "503" in msg
                or "502" in msg
                or "timeout" in msg.lower()
            )
            if retryable and attempt + 1 < max_attempts:
                wait_s = min(30.0, 2.0 ** attempt)
                logger.warning(
                    "Retry %d/%d for %s %s in %.1fs: %s",
                    attempt + 1,
                    max_attempts,
                    window.event_id,
                    symbol,
                    wait_s,
                    msg[:120],
                )
                time.sleep(wait_s)
                continue
            return window, symbol, None, exc
    return window, symbol, None, last_exc


def run_catalog_download(
    repo_root: Path,
    *,
    include_seed: bool = True,
    include_rule_based: bool = False,
    start_year: int = 2018,
    end_year: int = 2025,
    scope: str = "macro_releases",
    windows: list | None = None,
    symbols: tuple[str, ...] | None = None,
    max_cost_usd: float | None = None,
    limit: int | None = None,
    workers: int = 1,
    shard_index: int = 0,
    shard_count: int = 1,
    exclude_event_types: tuple[str, ...] | None = None,
    include_event_types: tuple[str, ...] | None = None,
    only_event_types: tuple[str, ...] | None = None,
    skip_if_npz: bool = True,
    source: str | None = None,
) -> DownloadReport:
    from economic_event_universe.events_csv_builder import resolve_download_scope_windows
    from economic_event_universe.registry import default_cme_symbols

    syms = symbols or default_cme_symbols()
    only_set = frozenset(only_event_types) if only_event_types else None
    exclusions = frozenset()
    if not only_set:
        exclusions = resolve_download_exclusions(
            exclude_event_types=exclude_event_types,
            include_event_types=include_event_types,
        )
    if windows is None:
        windows = resolve_download_scope_windows(
            repo_root,
            scope,
            start_year=start_year,
            end_year=end_year,
            include_seed=include_seed,
            include_rule_based=include_rule_based,
        )
    windows = filter_windows_by_event_type(
        windows,
        exclude_event_types=exclusions,
        only_event_types=only_set,
    )
    if only_set:
        logger.info("Only event types: %s", ", ".join(sorted(only_set)))
    elif exclusions:
        logger.info("Excluding event types from download: %s", ", ".join(sorted(exclusions)))

    from economic_event_universe.window_catalog import iter_missing_npz_slots

    missing_pairs = iter_missing_npz_slots(repo_root, windows, symbols=syms)
    report = DownloadReport(symbol_count=len(syms), products=list(syms))
    report.release_count = len({w.event_id for w, _ in missing_pairs})
    slot_count = 0

    tasks: list[tuple[Any, str]] = [(w, symbol) for w, symbol in missing_pairs]
    tasks = filter_tasks_for_shard(tasks, shard_index=shard_index, shard_count=shard_count)
    if limit is not None:
        tasks = tasks[:limit]

    worker_count = max(1, int(workers))
    if shard_count > 1:
        logger.info(
            "Shard %d/%d: %d slots assigned (of %d missing in scope)",
            shard_index,
            shard_count,
            len(tasks),
            len(missing_pairs),
        )
    if worker_count == 1:
        for window, symbol in tasks:
            window_obj, sym, result, exc = _download_slot_task(
                repo_root, window, symbol, max_cost_usd, skip_if_npz=skip_if_npz, source=source
            )
            _apply_slot_result(report, repo_root, window_obj, sym, result, exc=exc)
            if result is not None or exc is not None:
                slot_count += 1
        return report

    logger.info("Parallel MBO download: %d slots, %d workers", len(tasks), worker_count)
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [
            pool.submit(
                _download_slot_task,
                repo_root,
                window,
                symbol,
                max_cost_usd,
                skip_if_npz=skip_if_npz,
                source=source,
            )
            for window, symbol in tasks
        ]
        for fut in as_completed(futures):
            window_obj, sym, result, exc = fut.result()
            with _report_lock:
                _apply_slot_result(report, repo_root, window_obj, sym, result, exc=exc)
                if result is not None or exc is not None:
                    slot_count += 1

    return report
