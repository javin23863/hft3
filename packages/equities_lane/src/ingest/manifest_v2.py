"""Session bundle manifest v2 — factual audit rows only."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def manifest_v2_path(manifest_root: Path) -> Path:
    return manifest_root / "session_bundle_v2.json"


def load_manifest_v2(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 2, "sessions": []}
    return json.loads(path.read_text(encoding="utf-8"))


def upsert_session(manifest: dict[str, Any], row: dict[str, Any]) -> None:
    sessions = manifest.setdefault("sessions", [])
    sid = row.get("session_id")
    for i, existing in enumerate(sessions):
        if existing.get("session_id") == sid:
            merged = {**existing, **row}
            for key in ("equity", "options"):
                if key in row and key in existing and isinstance(row[key], dict):
                    merged[key] = {**existing.get(key, {}), **row[key]}
            sessions[i] = merged
            return
    sessions.append(row)


def write_manifest_v2(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def window_fields(start_utc: datetime, end_utc: datetime) -> dict[str, str]:
    return {
        "window_start_utc": start_utc.isoformat(),
        "window_end_utc": end_utc.isoformat(),
    }


def migrate_v1_row(v1: dict[str, Any]) -> dict[str, Any]:
    """Map legacy decadal_pull.json session into v2 equity block."""
    equity: dict[str, Any] = {}
    for key in ("raw_path", "normalized_path", "daily_path", "mbo_cost_usd", "daily_lookback_days", "daily_coverage_days"):
        if key in v1:
            equity[key] = v1[key]
    if v1.get("resolved_symbol"):
        equity["resolved_symbol"] = v1["resolved_symbol"]
    row: dict[str, Any] = {
        "session_id": v1.get("session_id"),
        "underlying": v1.get("symbol"),
        "date": v1.get("date"),
        "skip_pull": v1.get("status", "").startswith("skipped_"),
        "equity": equity,
    }
    if v1.get("notes"):
        row["skip_reason"] = v1.get("notes")
    return row


def migrate_v1_file(v1_path: Path, v2_path: Path) -> dict[str, Any]:
    manifest: dict[str, Any] = {"schema_version": 2, "sessions": []}
    if v1_path.exists():
        v1 = json.loads(v1_path.read_text(encoding="utf-8"))
        for row in v1.get("sessions", []):
            upsert_session(manifest, migrate_v1_row(row))
    if v2_path.exists():
        existing = load_manifest_v2(v2_path)
        for row in existing.get("sessions", []):
            upsert_session(manifest, row)
    manifest["migrated_at"] = datetime.now(timezone.utc).isoformat()
    write_manifest_v2(v2_path, manifest)
    return manifest
