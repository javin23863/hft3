#!/usr/bin/env python3
"""General resumable downloader for the futures EVENT-WINDOW universe.

Fills the missing-tape gap across ALL event types (minus an exclude set) for the
per-event symbol list in events.csv. Resumable + size-aware (treats sub-4KB
shells as missing so broken/empty NPZ get re-pulled) + cost-capped. Prices each
window with get_cost first, skips $0/holiday windows, aborts cleanly at the cap.

    # Tier C (default excludes the daily markers), capped:
    python scripts/download_event_tape.py --cost-cap 650
    # price only:
    python scripts/download_event_tape.py --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in [str(_REPO), str(_REPO / "packages")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data_system.src.keystore import load_keys  # noqa: E402
from data_system.src.events_parser import load_and_parse_events  # noqa: E402
from data_system.src.manifest_io import ManifestFileLock  # noqa: E402
from data_system.src.npz_resolver import npz_root  # noqa: E402
from backtest_pipeline.src.converter import DatabentoConverter  # noqa: E402
from data_system.src.databento_client import DatabentoResearchClient  # noqa: E402

EMBARGO = "2026-01-01"
EVENTS_CSV = _REPO / "packages" / "data_system" / "config" / "events.csv"
NPZ_ROOT = npz_root(_REPO)
RAW_DIR = _REPO / "data" / "raw" / "event_tape"
RECEIPT = _REPO / "runtime" / "databento" / "event_tape_download_receipt.json"
BUDGET_LEDGER = _REPO / "runtime" / "databento" / "event_tape_budget_ledger.jsonl"
SHELL_BYTES = 4096  # below this an NPZ is an empty/broken shell -> re-download
PRICE_TIMEOUT_S = 60      # watchdog on metadata.get_cost
DOWNLOAD_TIMEOUT_S = 120  # watchdog on timeseries.get_range (big windows take a while)

# Tier C: skip the high-volume daily markers (not econ releases) + the mislabeled one.
DEFAULT_EXCLUDE = "FACTORY_ORDERS,CASH_EQUITY_OPEN,FRIDAY_CLOSE"

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("event_tape")

# Bound any blocking socket op so a hung Databento connection raises
# socket.timeout (caught by _with_retry as transient -> bounded retry, then skip)
# instead of blocking a shard forever. Generous enough for large MBO windows.
socket.setdefaulttimeout(180)


class BudgetLedgerCorruptionError(ValueError):
    """Raised when the paid-data budget ledger cannot be trusted."""


def _present(event_id: str, sym: str) -> bool:
    p = NPZ_ROOT / f"{sym}_{event_id}_mbo.npz"
    return p.is_file() and p.stat().st_size >= SHELL_BYTES


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _request_identity(
    *,
    event_id: str,
    symbols: list[str],
    start_utc: datetime,
    end_utc: datetime,
    dataset: str = "GLBX.MDP3",
    schema: str = "mbo",
    stype_in: str = "continuous",
) -> dict:
    normalized_symbols = sorted({str(s).strip() for s in symbols if str(s).strip()})
    return {
        "event_id": str(event_id),
        "dataset": dataset,
        "schema": schema,
        "stype_in": stype_in,
        "start_utc": _utc_iso(start_utc),
        "end_utc": _utc_iso(end_utc),
        "requested_symbols": normalized_symbols,
        "resolved_symbols": normalized_symbols,
    }


def _identity_path(dbn_path: Path) -> Path:
    return dbn_path.with_name(f"{dbn_path.name}.request.json")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_identity(dbn_path: Path, request_identity: dict) -> None:
    stat = dbn_path.stat()
    payload = {
        "request": request_identity,
        "artifact": {
            "path": str(dbn_path),
            "size_bytes": int(stat.st_size),
            "sha256": _file_sha256(dbn_path),
        },
    }
    sidecar = _identity_path(dbn_path)
    sidecar.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _can_reuse_dbn(dbn_path: Path, request_identity: dict) -> bool:
    try:
        if not dbn_path.is_file() or dbn_path.stat().st_size <= SHELL_BYTES:
            return False
        payload = json.loads(_identity_path(dbn_path).read_text(encoding="utf-8"))
        if payload.get("request") != request_identity:
            return False
        artifact = payload.get("artifact") or {}
        if int(artifact.get("size_bytes", -1)) != int(dbn_path.stat().st_size):
            return False
        return artifact.get("sha256") == _file_sha256(dbn_path)
    except Exception:  # noqa: BLE001
        return False


def _converted_all_symbols(requested_symbols: list[str], converted_symbols: list[str]) -> bool:
    requested = {str(s).strip() for s in requested_symbols if str(s).strip()}
    converted = {str(s).strip() for s in converted_symbols if str(s).strip()}
    return requested.issubset(converted)


def _budget_symbol_set(value: object, *, entry_idx: int | None = None) -> set[str]:
    if not isinstance(value, list):
        where = f" entry {entry_idx}" if entry_idx is not None else ""
        raise BudgetLedgerCorruptionError(f"corrupt budget ledger{where}: missing requested_symbols")
    return {str(s).strip() for s in value if str(s).strip()}


def _cleanup_dbn_after_conversion(
    dbn_path: Path,
    *,
    keep_dbn: bool,
    requested_symbols: list[str],
    converted_symbols: list[str],
) -> None:
    if keep_dbn or not dbn_path.is_file():
        return
    if not _converted_all_symbols(requested_symbols, converted_symbols):
        return
    try:
        dbn_path.unlink()
        _identity_path(dbn_path).unlink(missing_ok=True)
    except OSError:
        pass


def _request_key(request_identity: dict) -> str:
    blob = json.dumps(request_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _read_budget_entries_unlocked(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    entries: list[dict] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BudgetLedgerCorruptionError(
                f"corrupt budget ledger {path} line {line_no}: invalid JSON ({exc.msg})"
            ) from exc
        if not isinstance(row, dict):
            raise BudgetLedgerCorruptionError(
                f"corrupt budget ledger {path} line {line_no}: expected JSON object"
            )
        entries.append(row)
    return entries


def _budget_costs_by_request(entries: list[dict]) -> dict[str, float]:
    costs: dict[str, float] = {}
    for idx, entry in enumerate(entries, start=1):
        key = entry.get("request_key")
        if not isinstance(key, str) or not key.strip():
            raise BudgetLedgerCorruptionError(f"corrupt budget ledger entry {idx}: missing request_key")
        try:
            cost = float(entry["cost_usd"])
        except KeyError as exc:
            raise BudgetLedgerCorruptionError(
                f"corrupt budget ledger entry {idx}: missing cost_usd"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise BudgetLedgerCorruptionError(
                f"corrupt budget ledger entry {idx}: invalid cost_usd"
            ) from exc
        costs[key] = max(costs.get(key, 0.0), cost)
    return costs


_BUDGET_COVERAGE_FIELDS = ("event_id", "dataset", "schema", "stype_in", "start_utc", "end_utc")


def _budget_entry_covers_request(entry: dict, request_identity: dict, idx: int) -> bool:
    paid_request = entry.get("request")
    if not isinstance(paid_request, dict):
        raise BudgetLedgerCorruptionError(f"corrupt budget ledger entry {idx}: missing request")
    if any(paid_request.get(field) != request_identity.get(field) for field in _BUDGET_COVERAGE_FIELDS):
        return False
    paid_symbols = _budget_symbol_set(paid_request.get("requested_symbols"), entry_idx=idx)
    requested_symbols = _budget_symbol_set(request_identity.get("requested_symbols"))
    return requested_symbols.issubset(paid_symbols)


def _budget_entry_overlap_conflict(entry: dict, request_identity: dict, idx: int) -> dict | None:
    paid_request = entry.get("request")
    if not isinstance(paid_request, dict):
        raise BudgetLedgerCorruptionError(f"corrupt budget ledger entry {idx}: missing request")
    if any(paid_request.get(field) != request_identity.get(field) for field in _BUDGET_COVERAGE_FIELDS):
        return None
    paid_symbols = _budget_symbol_set(paid_request.get("requested_symbols"), entry_idx=idx)
    requested_symbols = _budget_symbol_set(request_identity.get("requested_symbols"))
    overlap = requested_symbols & paid_symbols
    if not overlap or requested_symbols.issubset(paid_symbols):
        return None
    return {
        "entry_index": idx,
        "overlap_symbols": sorted(overlap),
        "paid_symbols": sorted(paid_symbols),
        "requested_symbols": sorted(requested_symbols),
    }


def _budget_request_covered(entries: list[dict], request_identity: dict) -> bool:
    return any(
        _budget_entry_covers_request(entry, request_identity, idx)
        for idx, entry in enumerate(entries, start=1)
    )


def _budget_request_overlap_conflict(entries: list[dict], request_identity: dict) -> dict | None:
    for idx, entry in enumerate(entries, start=1):
        conflict = _budget_entry_overlap_conflict(entry, request_identity, idx)
        if conflict is not None:
            return conflict
    return None


def _budget_attempt_recorded(path: Path, request_identity: dict) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    with ManifestFileLock(path, timeout_s=30.0):
        entries = _read_budget_entries_unlocked(path)
        key = _request_key(request_identity)
        costs = _budget_costs_by_request(entries)
        return (
            key in costs
            or _budget_request_covered(entries, request_identity)
            or _budget_request_overlap_conflict(entries, request_identity) is not None
        )


def _reserve_budget(
    path: Path,
    request_identity: dict,
    *,
    event_id: str,
    cost_usd: float,
    cost_cap_usd: float,
    shard: str | None = None,
) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    with ManifestFileLock(path, timeout_s=30.0):
        entries = _read_budget_entries_unlocked(path)
        costs = _budget_costs_by_request(entries)
        running = sum(costs.values())
        key = _request_key(request_identity)
        if key in costs or _budget_request_covered(entries, request_identity):
            return {"allowed": True, "duplicate": True, "running_usd": running, "after_usd": running}
        conflict = _budget_request_overlap_conflict(entries, request_identity)
        if conflict is not None:
            return {
                "allowed": False,
                "duplicate": False,
                "running_usd": running,
                "after_usd": running,
                "reason": "paid_symbol_overlap",
                "conflict": conflict,
            }
        after = running + float(cost_usd)
        if after > float(cost_cap_usd):
            return {"allowed": False, "duplicate": False, "running_usd": running, "after_usd": after}
        row = {
            "recorded_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "request_key": key,
            "event_id": event_id,
            "cost_usd": float(cost_usd),
            "cost_cap_usd": float(cost_cap_usd),
            "shard": shard,
            "request": request_identity,
            "state": "reserved_before_paid_download",
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        return {"allowed": True, "duplicate": False, "running_usd": running, "after_usd": after}


# Transient = worth retrying (concurrent-load / network blips). Everything else
# (422 symbology_invalid, "already exists", 404 no-data) is permanent for this
# window — retrying just burns wall-clock, so we re-raise on the first hit.
_TRANSIENT_MARKERS = (
    "429", "rate limit", "too many", "timeout", "timed out", "connection",
    "reset", "temporarily", "503", "502", "500", "504", "read timed",
)


def _with_retry(fn, *, tries: int = 4, base: float = 3.0):
    """Call fn(); retry ONLY transient errors with exponential backoff + jitter.

    Several shard processes hit the historical API concurrently, so 429 / 5xx /
    connection blips are expected and worth a retry. Permanent errors for a given
    window (422 symbology_invalid, file-already-exists, no-data 404) are re-raised
    immediately so the caller logs+skips fast instead of grinding 4 backoffs.
    """
    last: Exception | None = None
    for attempt in range(tries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            msg = str(exc).lower()
            transient = any(m in msg for m in _TRANSIENT_MARKERS)
            abandoned_timeout = isinstance(exc, TimeoutError) and "abandoned" in msg
            if abandoned_timeout:
                break
            if not transient or attempt == tries - 1:
                break
            rate_limited = "429" in msg or "rate" in msg or "too many" in msg
            delay = base * (2 ** attempt) * (3.0 if rate_limited else 1.0)
            delay += random.uniform(0, base)
            time.sleep(delay)
    assert last is not None
    raise last


def _call_with_timeout(fn, seconds):
    """Run fn() in a daemon thread; raise TimeoutError if it doesn't return in
    `seconds`. The hung thread is abandoned (daemon -> dies with the process).

    socket.setdefaulttimeout does NOT bound databento's HTTP client (observed:
    calls froze far past the default with no socket.timeout), so this hard
    wall-clock watchdog is the only reliable way to unstick a stalled request.
    Abandoned timeouts fail closed; retrying could issue the same paid request
    while the first in-flight request is still writing.
    """
    box: dict = {}

    def _run():
        try:
            box["r"] = fn()
        except BaseException as e:  # noqa: BLE001
            box["e"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise TimeoutError(f"call timed out after {seconds}s (abandoned)")
    if "e" in box:
        raise box["e"]
    return box.get("r")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exclude-types", default=DEFAULT_EXCLUDE)
    ap.add_argument("--cost-cap", type=float, default=650.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-dbn", action="store_true")
    ap.add_argument("--shard", default=None, metavar="I/N",
                    help="Run only shard I of N (rows where row_index %% N == I). "
                         "Disjoint event sets across shards -> safe to run N copies in parallel.")
    ap.add_argument("--start-idx", type=int, default=0, dest="start_idx",
                    help="Skip all rows with idx < START_IDX. Jumps straight past the "
                         "already-filled/unfillable head to a tail region without re-running its gap probes.")
    args = ap.parse_args()
    exclude = {t.strip() for t in args.exclude_types.split(",") if t.strip()}

    shard_i = shard_n = None
    if args.shard:
        _parts = args.shard.split("/")
        if len(_parts) != 2:
            ap.error(f"--shard must be 'I/N' (e.g. 0/6), got {args.shard!r}")
        shard_i, shard_n = int(_parts[0]), int(_parts[1])
        if not (shard_n >= 1 and 0 <= shard_i < shard_n):
            ap.error(f"--shard needs N >= 1 and 0 <= I < N, got {args.shard!r}")

    receipt = RECEIPT if shard_n is None else RECEIPT.with_name(
        f"event_tape_download_receipt_shard{shard_i}of{shard_n}.json")

    load_keys()
    NPZ_ROOT.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    client = DatabentoResearchClient()
    converter = DatabentoConverter(output_dir=str(NPZ_ROOT))

    ev = load_and_parse_events(str(EVENTS_CSV))
    ev = ev[ev["release_date"] < EMBARGO].copy().sort_values("release_date").reset_index(drop=True)
    ev = ev[~ev["event_type"].isin(exclude)].reset_index(drop=True)
    log.info("Excluding types: %s", sorted(exclude))
    log.info("Candidate events (pre-embargo, post-exclude): %d", len(ev))
    if shard_n is not None:
        log.info("Shard %d/%d active (handling row indices where idx %% %d == %d)", shard_i, shard_n, shard_n, shard_i)

    processed = empty = skipped = written = 0
    running = 0.0
    per_sym: dict[str, int] = {}
    n = len(ev)

    for idx, row in ev.iterrows():
        if idx < args.start_idx:
            continue
        if shard_n is not None and (idx % shard_n) != shard_i:
            continue
        eid = str(row["event_id"])
        syms = [s.strip() for s in str(row.get("symbols", "")).replace(";", ",").split(",") if s.strip()]
        missing = [s for s in syms if not _present(eid, s)]
        if not missing:
            skipped += 1
            continue
        start = row["start_utc"].to_pydatetime()
        end = row["end_utc"].to_pydatetime()
        dbn = RAW_DIR / f"{eid}_mbo.dbn.zst"
        request_identity = _request_identity(event_id=eid, symbols=missing, start_utc=start, end_utc=end)
        # Raw tape already on disk from a prior --keep-dbn run: convert straight
        # from it only when the request sidecar proves exact identity and artifact
        # integrity. Event id alone is not enough: symbol set, window, schema,
        # stype, size, and checksum all have to match.
        reused = _can_reuse_dbn(dbn, request_identity)
        cost = 0.0
        if reused:
            if args.dry_run:
                continue
            dest = str(dbn)
        else:
            try:
                cost = float(_with_retry(lambda: _call_with_timeout(
                    lambda: client.estimate_cost(symbols=missing, start_utc=start, end_utc=end),
                    PRICE_TIMEOUT_S)))
            except Exception as exc:
                log.warning("[%d/%d] %s price failed: %s", idx + 1, n, eid, exc)
                empty += 1
                continue
            if cost == 0.0:
                empty += 1
                continue
            if args.dry_run:
                if running + cost > args.cost_cap:
                    log.warning("DRY-RUN COST CAP $%.2f would be exceeded (running $%.2f + $%.4f). Stopping cleanly at %d/%d (%s).",
                                args.cost_cap, running, cost, idx + 1, n, eid)
                    break
                running += cost
                processed += 1
                continue
            if _budget_attempt_recorded(BUDGET_LEDGER, request_identity) and not _can_reuse_dbn(dbn, request_identity):
                log.error("[%d/%d] %s previous paid attempt exists without a matching DBN sidecar; refusing to reissue until reconciled",
                          idx + 1, n, eid)
                empty += 1
                continue
            reservation = _reserve_budget(
                BUDGET_LEDGER,
                request_identity,
                event_id=eid,
                cost_usd=cost,
                cost_cap_usd=args.cost_cap,
                shard=args.shard,
            )
            if not reservation["allowed"]:
                if reservation.get("reason") == "paid_symbol_overlap":
                    log.error("[%d/%d] %s previous paid ledger overlap without exact DBN proof; refusing to reissue symbols=%s conflict=%s",
                              idx + 1, n, eid, missing, reservation.get("conflict"))
                    empty += 1
                    continue
                log.warning("GLOBAL COST CAP $%.2f would be exceeded (ledger $%.2f + $%.4f). Stopping cleanly at %d/%d (%s). Re-run after budget review.",
                            args.cost_cap, reservation["running_usd"], cost, idx + 1, n, eid)
                break
            if reservation["duplicate"]:
                if _can_reuse_dbn(dbn, request_identity):
                    dest = str(dbn)
                    reused = True
                    cost = 0.0
                    log.info("[%d/%d] %s duplicate budget reservation -> converting identity-checked .dbn",
                             idx + 1, n, eid)
                else:
                    log.error("[%d/%d] %s duplicate paid attempt without a matching DBN sidecar; refusing to reissue until reconciled",
                              idx + 1, n, eid)
                    empty += 1
                    continue
            else:
                try:
                    dest = _with_retry(lambda: _call_with_timeout(
                        lambda: client.download_event_window(
                            event_id=eid, symbols=missing, start_utc=start, end_utc=end,
                            output_path=str(dbn), override_operating_cap=True, override_hard_limit=False,
                            cost_estimate=cost),
                        DOWNLOAD_TIMEOUT_S))
                except Exception as exc:
                    # Raw .dbn already on disk (prior run / concurrent shard write):
                    # convert it only if the sidecar proves this exact request.
                    if _can_reuse_dbn(dbn, request_identity):
                        dest = str(dbn)
                        reused = True
                        cost = 0.0
                        log.info("[%d/%d] %s download blocked (%s) -> converting identity-checked .dbn",
                                 idx + 1, n, eid, str(exc).splitlines()[0][:60])
                    else:
                        log.error("[%d/%d] %s download failed: %s", idx + 1, n, eid, exc)
                        empty += 1
                        continue
        dbn = Path(dest) if dest else dbn
        if not reused and dbn.is_file():
            _write_identity(dbn, request_identity)
        conv = []
        for s in missing:
            try:
                converter.convert_file(str(dbn), s)
                conv.append(s)
                per_sym[s] = per_sym.get(s, 0) + 1
                written += 1
            except Exception as exc:
                log.warning("  convert %s/%s failed (thin/no records): %s", eid, s, exc)
        running += cost
        processed += 1
        log.info("[%d/%d] %s %scost=$%.4f conv=%s running=$%.2f", idx + 1, n, eid,
                 "REUSED-dbn " if reused else "", cost, conv or "[]", running)
        _cleanup_dbn_after_conversion(
            dbn,
            keep_dbn=args.keep_dbn,
            requested_symbols=missing,
            converted_symbols=conv,
        )

    summary = {
        "run_utc": datetime.now(timezone.utc).isoformat(), "dry_run": args.dry_run,
        "excluded_types": sorted(exclude), "windows_processed": processed, "windows_empty": empty,
        "windows_skipped_existing": skipped, "symbols_npz_written": written,
        "total_cost_usd": round(running, 6), "cost_cap": args.cost_cap,
        "npz_root": str(NPZ_ROOT), "budget_ledger": str(BUDGET_LEDGER),
        "per_symbol_file_counts": per_sym,
    }
    print("\n" + "=" * 70)
    print(f"EVENT TAPE DOWNLOAD  (dry_run={args.dry_run})")
    print(f"  processed {processed}  empty/no-data {empty}  skipped-existing {skipped}  npz_written {written}")
    print(f"  TOTAL COST: ${running:.4f}  (cap ${args.cost_cap})")
    if not args.dry_run:
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print(f"  receipt: {receipt}")
    print("=" * 70)


if __name__ == "__main__":
    main()
