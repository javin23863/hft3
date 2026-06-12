"""Atomic, cross-process-safe manifest.parquet append."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_local_lock = threading.Lock()

_DEFAULT_AVG_COST = 0.0309


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _try_clear_stale_lock(lock_path: Path) -> None:
    """Remove lock file if holder PID is dead (crash/kill left stale lock)."""
    try:
        raw = lock_path.read_bytes()
    except OSError:
        return
    try:
        holder = int(raw.decode().strip())
    except ValueError:
        holder = -1
    if holder == os.getpid() or _pid_alive(holder):
        return
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


class ManifestFileLock:
    """Exclusive lock via O_CREAT|O_EXCL (works on Windows and POSIX)."""

    def __init__(self, manifest_path: str | Path, *, timeout_s: float = 120.0):
        self._lock_path = Path(f"{manifest_path}.lock")
        self._timeout_s = timeout_s
        self._fd: int | None = None

    def __enter__(self) -> ManifestFileLock:
        deadline = time.monotonic() + self._timeout_s
        while time.monotonic() < deadline:
            _try_clear_stale_lock(self._lock_path)
            try:
                self._fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                time.sleep(0.1)
        raise TimeoutError(f"Timed out waiting for manifest lock: {self._lock_path}")

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self._lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def _backup_corrupt(manifest_path: Path) -> None:
    if not manifest_path.is_file():
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = manifest_path.with_name(f"{manifest_path.name}.corrupt.{stamp}.bak")
    os.replace(manifest_path, backup)


def read_manifest(manifest_path: str | Path) -> pd.DataFrame:
    path = Path(manifest_path)
    if not path.is_file():
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except Exception:
        return pd.DataFrame()


def read_manifest_locked(manifest_path: str | Path) -> pd.DataFrame:
    """Thread-safe read; pairs with append_manifest_record in-process."""
    with _local_lock:
        return read_manifest(manifest_path)


def _atomic_replace(tmp: Path, dest: Path, *, retries: int = 30, delay_s: float = 0.1) -> None:
    """os.replace with retries for Windows readers holding manifest open."""
    last_err: OSError | None = None
    for _ in range(retries):
        try:
            os.replace(tmp, dest)
            return
        except OSError as exc:
            last_err = exc
            if exc.errno not in (5, 13) and not isinstance(exc, PermissionError):
                raise
            time.sleep(delay_s)
    if last_err is not None:
        raise last_err


def append_manifest_record(manifest_path: str | Path, record: dict) -> None:
    path = Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _local_lock:
        with ManifestFileLock(path):
            df_new = pd.DataFrame([record])
            df_existing = read_manifest(path)
            if df_existing.empty and path.is_file():
                _backup_corrupt(path)
                df_existing = pd.DataFrame()
            df_combined = pd.concat([df_existing, df_new], ignore_index=True) if not df_existing.empty else df_new
            tmp = path.with_suffix(".parquet.tmp")
            df_combined.to_parquet(tmp, index=False)
            _atomic_replace(tmp, path)


def default_manifest_path(repo_root: Path | None = None) -> Path:
    """Spend-ledger location: HFT3_MANIFEST_PATH env (canonical lake ledger,
    e.g. C:\\hft3-lake\\manifest.parquet) with <repo>/data/manifest.parquet fallback."""
    env = os.environ.get("HFT3_MANIFEST_PATH", "").strip()
    if env:
        return Path(env)
    base = Path(repo_root) if repo_root is not None else Path.cwd()
    return base / "data" / "manifest.parquet"


def rebuild_from_mbo_release_slots(
    repo_root: Path,
    manifest_path: str | Path | None = None,
    *,
    avg_cost_usd: float = _DEFAULT_AVG_COST,
) -> dict:
    """Backfill manifest rows for local raw slots missing from the ledger."""
    repo_root = Path(repo_root)
    if manifest_path is None:
        path = default_manifest_path(repo_root)
    else:
        path = Path(manifest_path)
        if not path.is_absolute():
            path = repo_root / path
    existing = read_manifest(path)
    if existing.empty and path.is_file():
        _backup_corrupt(path)
        existing = pd.DataFrame()

    billed: set[tuple[str, str]] = set()
    if not existing.empty and "event_id" in existing.columns:
        for _, row in existing.iterrows():
            eid = str(row.get("event_id", "")).strip()
            req = str(row.get("requested_symbol", "") or row.get("symbols", "")).strip()
            sym = req.replace("[", "").replace("]", "").replace("'", "").split(",")[0].strip()
            if eid and sym:
                billed.add((eid, sym))

    # Lake-aware raw-slot roots: <lake>/mbo_release first (HFT3_NPZ_ROOT
    # parent), then the repo-relative <repo>/data/mbo_release fallback.
    from data_system.src.event_data_resolver import mbo_release_search_roots

    added = 0
    rows: list[dict] = []
    now = datetime.now(timezone.utc)
    for root in mbo_release_search_roots(repo_root):
        if not root.is_dir():
            continue
        from mbo_release_lane.storage import iter_release_manifest_paths

        seen_raw: set[Path] = set()
        for manifest in iter_release_manifest_paths(root):
            slot = manifest.parent
            raw = slot / "raw.dbn.zst"
            if raw in seen_raw:
                continue
            seen_raw.add(raw)
            try:
                if not raw.is_file() or raw.stat().st_size == 0:
                    continue
            except OSError:
                continue
            eid, sym = raw.parts[-3], raw.parts[-2]
            key = (eid, sym)
            if key in billed:
                continue
            rows.append(
                {
                    "event_id": eid,
                    "symbols": f"['{sym}']",
                    "requested_symbol": sym,
                    "resolved_symbol": sym,
                    "start_utc": now,
                    "end_utc": now,
                    "cost": avg_cost_usd,
                    "duration_seconds": 70.0,
                    "cost_per_symbol_minute": avg_cost_usd / (70.0 / 60.0),
                    "output_path": (
                        str(raw.relative_to(repo_root)) if raw.is_relative_to(repo_root) else str(raw)
                    ).replace("\\", "/"),
                    "dataset": "GLBX.MDP3",
                    "schema": "mbo",
                    "download_time": now,
                    "rebuilt_from_raw": True,
                }
            )
            billed.add(key)
            added += 1

    if rows:
        with ManifestFileLock(path):
            df_existing = read_manifest(path)
            df_combined = pd.concat([df_existing, pd.DataFrame(rows)], ignore_index=True)
            tmp = path.with_suffix(".parquet.tmp")
            df_combined.to_parquet(tmp, index=False)
            os.replace(tmp, path)

    final = read_manifest(path)
    spend = float(final["cost"].sum()) if not final.empty and "cost" in final.columns else 0.0
    return {
        "manifest_path": str(path),
        "rows_before": len(existing),
        "rows_added_from_raw": added,
        "rows_after": len(final),
        "spend_usd": round(spend, 4),
    }
