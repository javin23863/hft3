"""Inventory every data file in the repo + HFT3_PAID_DATA_ROOT.

Walks both data roots, categorizes files by lane, computes a SHA-256 prefix
for small files (integrity hint), and cross-references against the priority
lane coverage JSON to surface orphans and expected-but-missing slots.

Outputs:
    runtime/data_audits/repo_data_inventory.json
    runtime/data_audits/repo_data_inventory.md

Lane attribution rules (kept simple; extend as new lanes are added):
    mbo_release    data/mbo_release/, data/raw/ legacy
    npz            data/npz/
    sensors        data/sensors/
    equities       data/equities/
    options        data/options/, data/vix_options/
    crypto         data/crypto/
    raw            data/raw/ (top-level)
    replay         data/replay/
    normalized     data/normalized/
    latency        data/crypto/latency/, data/latency_baselines/
    manifest       data/manifest.parquet
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SHA_MAX_BYTES = 100 * 1024 * 1024  # 100 MiB; skip SHA for larger files
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _safe_resolve(p: Path) -> str:
    try:
        return str(p.resolve())
    except OSError:
        return str(p)


def _lane_for(rel: str) -> str:
    parts = rel.replace("\\", "/").split("/")
    if not parts:
        return "unknown"
    top = parts[0]
    if top == "mbo_release":
        return "mbo_release"
    if top == "npz":
        return "npz"
    if top == "sensors":
        return "sensors"
    if top == "equities":
        return "equities"
    if top in ("options", "vix_options"):
        return "options"
    if top == "crypto":
        if len(parts) > 2 and parts[1] == "latency":
            return "latency"
        return "crypto"
    if top == "raw":
        return "raw"
    if top == "replay":
        return "replay"
    if top == "normalized":
        return "normalized"
    if top == "latency_baselines":
        return "latency"
    if top.endswith(".dbn.zst") or top.endswith(".parquet") or top == "manifest.parquet":
        return "manifest"
    return "other"


def _sha_prefix(path: Path) -> str | None:
    try:
        if path.stat().st_size > _SHA_MAX_BYTES:
            return None
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except OSError:
        return None


def _walk(root: Path, *, follow: bool = False) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    out: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow):
        dirnames.sort()
        filenames.sort()
        for name in filenames:
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                continue
            rel = p.relative_to(root).as_posix()
            out.append({
                "rel": rel,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, UTC).isoformat(),
                "sha256_prefix": _sha_prefix(p),
            })
    return out


def _categorize(files: list[dict[str, Any]], root_label: str) -> dict[str, list[dict[str, Any]]]:
    by_lane: dict[str, list[dict[str, Any]]] = {}
    for f in files:
        lane = _lane_for(f["rel"])
        by_lane.setdefault(lane, []).append({**f, "root": root_label, "lane": lane})
    return by_lane


def _cross_ref_priority(by_lane: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    cov_path = _REPO_ROOT / "runtime" / "data_audits" / "priority_lane_coverage.json"
    if not cov_path.is_file():
        return {"available": False}
    try:
        cov = json.loads(cov_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"available": False, "error": "priority_lane_coverage.json unreadable"}
    mbo = cov.get("mbo", {})
    npz_paths = {Path(f["rel"]).name for f in by_lane.get("npz", [])}
    mbo_release_event_ids: set[str] = set()
    mbo_release_symbols: set[str] = set()
    for f in by_lane.get("mbo_release", []):
        parts = Path(f["rel"]).parts
        if len(parts) >= 2:
            mbo_release_event_ids.add(parts[1])
        if len(parts) >= 3:
            mbo_release_symbols.add(parts[2])
    sensor_event_ids: set[str] = set()
    for f in by_lane.get("sensors", []):
        parts = Path(f["rel"]).parts
        if len(parts) >= 2:
            sub = parts[1]
            if sub.endswith("_sensors"):
                sensor_event_ids.add(sub[: -len("_sensors")])
            else:
                sensor_event_ids.add(sub)
    incomplete = mbo.get("incomplete_sample", [])
    return {
        "available": True,
        "generated_at_utc": cov.get("generated_at_utc"),
        "mbo_total_slots": mbo.get("total_slots"),
        "mbo_complete": mbo.get("complete"),
        "mbo_complete_pct": mbo.get("complete_pct"),
        "mbo_status_counts": mbo.get("status_counts", {}),
        "npz_filename_count": len(npz_paths),
        "mbo_release_event_id_count": len(mbo_release_event_ids),
        "mbo_release_symbol_count": len(mbo_release_symbols),
        "sensor_event_id_count": len(sensor_event_ids),
        "incomplete_sample_size": len(incomplete),
    }


def _expected_but_missing(by_lane: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Surface dirs that are present but contain no data files (decadal pull target)."""
    notes: list[str] = []
    eq_files = by_lane.get("equities", [])
    if not eq_files:
        notes.append("data/equities/ has no data files (subdirs are scaffolding; decadal pull needed).")
    else:
        eq_subdirs: set[str] = set()
        for f in eq_files:
            parts = Path(f["rel"]).parts
            if len(parts) >= 3 and not parts[1].startswith("."):
                eq_subdirs.add(parts[1])
        notes.append(f"data/equities/ files in subdirs: {sorted(eq_subdirs)} (empty subdirs are scaffolding)")
    if not by_lane.get("options"):
        notes.append("data/options/ has no data files (subdirs scaffolding).")
    if not by_lane.get("sensors"):
        notes.append("data/sensors/ has no data files (525/879 VIX sensors broken per handoff).")
    return {"notes": notes}


def _write_markdown(inv: dict[str, Any], path: Path) -> None:
    lines: list[str] = []
    lines.append("# Repo data inventory")
    lines.append("")
    lines.append(f"**Generated:** {inv['generated_at_utc']}")
    lines.append(f"**Roots scanned:** {', '.join(inv['roots_scanned'])}")
    lines.append(f"**Total files:** {inv['total_files']}  ")
    lines.append(f"**Total bytes:** {inv['total_bytes']:,}  ")
    lines.append("")
    lines.append("## By lane")
    lines.append("")
    lines.append("| Lane | Files | Bytes |")
    lines.append("|------|------:|------:|")
    for lane, stats in sorted(inv["by_lane_stats"].items(), key=lambda kv: -kv[1]["count"]):
        lines.append(f"| {lane} | {stats['count']:,} | {stats['bytes']:,} |")
    lines.append("")
    lines.append("## By root")
    lines.append("")
    lines.append("| Root | Files | Bytes |")
    lines.append("|------|------:|------:|")
    for r, stats in inv["by_root_stats"].items():
        lines.append(f"| {r} | {stats['count']:,} | {stats['bytes']:,} |")
    lines.append("")
    cr = inv.get("priority_cross_ref", {})
    if cr.get("available"):
        lines.append("## Priority lane cross-ref (priority_lane_coverage.json)")
        lines.append("")
        lines.append(f"- Snapshot: `{cr.get('generated_at_utc')}`")
        lines.append(f"- MBO slots: {cr.get('mbo_complete')}/{cr.get('mbo_total_slots')} ({cr.get('mbo_complete_pct')}%)")
        sc = cr.get("mbo_status_counts", {})
        lines.append(f"- status_counts: {sc}")
        lines.append(f"- NPZ filenames: {cr.get('npz_filename_count')}")
        lines.append(f"- mbo_release event_ids: {cr.get('mbo_release_event_id_count')}")
        lines.append(f"- mbo_release symbols: {cr.get('mbo_release_symbol_count')}")
        lines.append(f"- sensor event_ids: {cr.get('sensor_event_id_count')}")
        lines.append("")
    ebm = inv.get("expected_but_missing", {})
    if ebm.get("notes"):
        lines.append("## Expected-but-missing")
        lines.append("")
        for n in ebm["notes"]:
            lines.append(f"- {n}")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="inventory_repo_data")
    parser.add_argument("--out-dir", default="runtime/data_audits")
    parser.add_argument("--no-sha", action="store_true", help="Skip SHA-256 prefix computation")
    args = parser.parse_args(argv)
    if args.no_sha:
        global _sha_prefix
        _sha_prefix = lambda _p: None  # type: ignore[assignment]

    try:
        from dotenv import load_dotenv
        load_dotenv(_REPO_ROOT / ".env", override=False)
    except Exception as exc:
        print(f"warn: dotenv load failed: {exc}", file=sys.stderr)

    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from hft3_bootstrap import setup_repo_paths
        setup_repo_paths()
    except Exception as exc:
        print(f"warn: hft3_bootstrap.setup_repo_paths failed: {exc}", file=sys.stderr)
    try:
        from data_system.src.data_roots import paid_data_root
        paid = paid_data_root(_REPO_ROOT)
    except Exception as exc:
        print(f"warn: paid_data_root resolution failed: {exc}", file=sys.stderr)
        paid = (_REPO_ROOT / "data").resolve()

    repo_data = (_REPO_ROOT / "data").resolve()
    seen_roots: set[str] = set()
    roots: list[tuple[str, Path]] = []
    if repo_data.exists():
        seen_roots.add(str(repo_data).lower())
        roots.append(("repo", repo_data))
    if paid.exists() and str(paid).lower() not in seen_roots:
        seen_roots.add(str(paid).lower())
        roots.append(("paid", paid))
    elif paid.exists() and str(paid).lower() in seen_roots:
        roots.append(("paid-skipped(==repo)", paid))
    roots_scanned: list[str] = []
    all_files: list[dict[str, Any]] = []
    by_root_stats: dict[str, dict[str, int]] = {}
    for label, root in roots:
        if not root.exists() or label.startswith("paid-skipped"):
            by_root_stats[label] = {"count": 0, "bytes": 0}
            continue
        roots_scanned.append(f"{label}={_safe_resolve(root)}")
        files = _walk(root)
        cnt = len(files)
        bts = sum(f["size"] for f in files)
        by_root_stats[label] = {"count": cnt, "bytes": bts}
        for f in files:
            f["root"] = label
            f["abs_path"] = _safe_resolve(root / f["rel"])
            f["lane"] = _lane_for(f["rel"])
        all_files.extend(files)

    by_lane: dict[str, list[dict[str, Any]]] = {}
    for f in all_files:
        by_lane.setdefault(f["lane"], []).append(f)
    by_lane_stats: dict[str, dict[str, int]] = {}
    for lane, lst in by_lane.items():
        by_lane_stats[lane] = {
            "count": len(lst),
            "bytes": sum(f["size"] for f in lst),
        }

    inv: dict[str, Any] = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "roots_scanned": roots_scanned,
        "total_files": len(all_files),
        "total_bytes": sum(f["size"] for f in all_files),
        "by_root_stats": by_root_stats,
        "by_lane_stats": by_lane_stats,
        "priority_cross_ref": _cross_ref_priority(by_lane),
        "expected_but_missing": _expected_but_missing(by_lane),
        "files": all_files,
    }

    out_dir = _REPO_ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "repo_data_inventory.json"
    md_path = out_dir / "repo_data_inventory.md"
    json_path.write_text(json.dumps(inv, indent=2, default=str), encoding="utf-8")
    _write_markdown(inv, md_path)
    print(f"wrote {json_path}  ({inv['total_files']:,} files, {inv['total_bytes']:,} bytes)")
    print(f"wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
