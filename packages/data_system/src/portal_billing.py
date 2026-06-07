"""Databento portal-style billing from on-disk bytes (GB × rate), not manifest.cost."""

from __future__ import annotations

import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

# Portal historical streaming rates ($/GB) — match Databento invoice line items.
PORTAL_USD_PER_GB: dict[tuple[str, str], float] = {
    ("GLBX.MDP3", "mbo"): 1.80,
    ("GLBX.MDP3", "mbp-1"): 1.80,
    ("GLBX.MDP3", "mbp-10"): 0.50,
    ("OPRA.PILLAR", "cbbo-1m"): 2.00,
    ("XNAS.ITCH", "mbo"): 1.20,
    ("XNAS.ITCH", "imbalance"): 16.00,
    ("XNAS.ITCH", "ohlcv-1d"): 30.00,
    ("XNYS.PILLAR", "mbo"): 1.20,
    ("XNYS.PILLAR", "imbalance"): 16.00,
    ("XNYS.PILLAR", "ohlcv-1d"): 30.00,
}

PORTAL_LINE_LABELS: dict[tuple[str, str], str] = {
    ("GLBX.MDP3", "mbo"): "CME Globex MDP 3.0 / MBO",
    ("GLBX.MDP3", "mbp-1"): "CME Globex MDP 3.0 / MBP-1",
    ("GLBX.MDP3", "mbp-10"): "CME Globex MDP 3.0 / MBP-10",
    ("OPRA.PILLAR", "cbbo-1m"): "OPRA / CBBO-1m",
    ("XNAS.ITCH", "mbo"): "Nasdaq TotalView-ITCH / MBO",
    ("XNAS.ITCH", "imbalance"): "Nasdaq TotalView-ITCH / Imbalance",
    ("XNAS.ITCH", "ohlcv-1d"): "Nasdaq TotalView-ITCH / OHLCV-1d",
    ("XNYS.PILLAR", "mbo"): "NYSE Integrated / MBO",
    ("XNYS.PILLAR", "imbalance"): "NYSE Integrated / Imbalance",
    ("XNYS.PILLAR", "ohlcv-1d"): "NYSE Integrated / OHLCV-1d",
}

GB = 1024**3


@dataclass(frozen=True)
class BillableFile:
    path: Path
    dataset: str
    schema: str
    dedupe_key: tuple[str, ...]
    bytes: int
    host: str


@dataclass
class PortalBucket:
    dataset: str
    schema: str
    bytes: int = 0
    files: int = 0
    get_cost_usd: float = 0.0
    usd_per_gb: float = 0.0

    @property
    def gb_from_bytes(self) -> float:
        return self.bytes / GB

    @property
    def usd_from_bytes(self) -> float:
        return self.gb_from_bytes * self.usd_per_gb

    @property
    def implied_gb_from_get_cost(self) -> float:
        if self.usd_per_gb <= 0:
            return 0.0
        return self.get_cost_usd / self.usd_per_gb


def _slot_key(row) -> tuple[str, str, str, str]:
    eid = str(row.get("event_id", "")).strip()
    ds = str(row.get("dataset", "GLBX.MDP3")).strip()
    schema = str(row.get("schema", "mbo")).strip()
    req = str(row.get("requested_symbol", "") or row.get("symbols", "")).strip()
    sym = req.replace("[", "").replace("]", "").replace("'", "").split(",")[0].strip()
    return (ds, schema, eid, sym)


def _parse_equities_raw(path: Path) -> tuple[str, str, tuple[str, ...]] | None:
    stem = path.name.replace(".dbn.zst", "")
    parts = stem.rsplit("_", 1)
    if len(parts) != 2:
        return None
    prefix, schema = parts
    bits = prefix.rsplit("_", 1)
    if len(bits) != 2:
        return None
    symbol, date = bits
    dataset = "OPRA.PILLAR" if schema == "cbbo-1m" else "XNAS.ITCH"
    return dataset, schema, (dataset, schema, symbol, date)


def _classify_local_file(repo_root: Path, path: Path) -> tuple[str, str, tuple[str, ...]] | None:
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        return None
    parts = rel.parts
    if (
        len(parts) >= 5
        and parts[0] == "data"
        and parts[1] == "mbo_release"
        and path.name == "raw.dbn.zst"
    ):
        return "GLBX.MDP3", "mbo", ("GLBX.MDP3", "mbo", parts[2], parts[3])
    if len(parts) >= 4 and parts[0] == "data" and parts[1] == "equities" and parts[2] == "raw":
        return _parse_equities_raw(path)
    if len(parts) >= 4 and parts[0] == "data" and parts[1] == "options" and path.name.endswith(".dbn.zst"):
        stem = path.name.replace(".dbn.zst", "")
        bits = stem.rsplit("_", 1)
        if len(bits) == 2:
            session_id, schema = bits
            if schema == "cbbo-1m":
                return "OPRA.PILLAR", schema, ("OPRA.PILLAR", schema, session_id)
    return None


def iter_local_billable_files(repo_root: Path) -> Iterator[BillableFile]:
    repo_root = Path(repo_root)
    for pattern in (
        "data/mbo_release/*/*/raw.dbn.zst",
        "data/equities/raw/*.dbn.zst",
        "data/options/**/*.dbn.zst",
    ):
        for path in repo_root.glob(pattern):
            if not path.is_file() or path.stat().st_size == 0:
                continue
            classified = _classify_local_file(repo_root, path)
            if not classified:
                continue
            dataset, schema, key = classified
            yield BillableFile(path, dataset, schema, key, path.stat().st_size, "local")


def fetch_chi404_mbo_files(repo_prefix: str = "/root/hft3/repo") -> dict[tuple[str, ...], int]:
    cmd = (
        f"find {repo_prefix}/data/mbo_release -type f -name raw.dbn.zst -size +0 "
        r"-printf '%p %s\n' 2>/dev/null"
    )
    try:
        proc = subprocess.run(
            ["ssh", "chi404", cmd],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    out: dict[tuple[str, ...], int] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or " " not in line:
            continue
        path_str, size_str = line.rsplit(" ", 1)
        try:
            size = int(size_str)
        except ValueError:
            continue
        path_str = path_str.replace("\\", "/")
        idx = path_str.find("data/mbo_release/")
        if idx < 0:
            continue
        tail = path_str[idx + len("data/mbo_release/") :]
        parts = tail.split("/")
        if len(parts) < 3:
            continue
        event_id, symbol = parts[0], parts[1]
        out[("GLBX.MDP3", "mbo", event_id, symbol)] = size
    return out


def _load_manifest_df(path: Path):
    import pandas as pd

    if not path.is_file():
        return None
    try:
        return pd.read_parquet(path)
    except Exception:
        return None


def _dedupe_manifest_frames(frames: list) -> Any:
    import pandas as pd

    if not frames:
        return pd.DataFrame()
    all_df = pd.concat(frames, ignore_index=True)
    if "rebuilt_from_raw" in all_df.columns:
        all_df = all_df[all_df["rebuilt_from_raw"] != True]  # noqa: E712
    all_df["_slot"] = all_df.apply(_slot_key, axis=1)
    if "download_time" in all_df.columns:
        all_df = all_df.sort_values("download_time")
    # Prefer local manifest row when both hosts recorded the same slot.
    if "_host" in all_df.columns:
        all_df = all_df.sort_values(["_slot", "_host"])
    return all_df.drop_duplicates("_slot", keep="last")


def manifest_get_cost_summary(repo_root: Path, *, chi404_manifest: Path | None = None) -> dict[str, Any]:
    repo_root = Path(repo_root)
    frames = []
    meta = []
    for label, path in (
        ("local", repo_root / "data" / "manifest.parquet"),
        ("chi404", chi404_manifest or repo_root / "runtime/data_downloads/chi404_manifest.parquet"),
    ):
        df = _load_manifest_df(path)
        if df is None or df.empty:
            continue
        df = df.copy()
        df["_host"] = label
        frames.append(df)
        meta.append((label, len(df), float(df["cost"].sum()) if "cost" in df.columns else 0.0))

    if not frames:
        return {
            "local_rows": 0,
            "chi404_rows": 0,
            "naive_sum_usd": 0.0,
            "deduped_slots": 0,
            "deduped_get_cost_usd": 0.0,
            "overlap_slots": 0,
            "duplicate_row_overhead_usd": 0.0,
        }

    import pandas as pd

    all_df = pd.concat(frames, ignore_index=True)
    naive_sum = float(all_df["cost"].sum()) if "cost" in all_df.columns else 0.0
    local_rows = int((all_df["_host"] == "local").sum()) if "_host" in all_df.columns else len(all_df)
    chi404_rows = int((all_df["_host"] == "chi404").sum()) if "_host" in all_df.columns else 0

    all_df["_slot"] = all_df.apply(_slot_key, axis=1)
    overlap = int(all_df.groupby("_slot")["_host"].nunique().gt(1).sum()) if "_host" in all_df.columns else 0

    deduped = _dedupe_manifest_frames(frames)
    deduped_cost = float(deduped["cost"].sum()) if "cost" in deduped.columns and len(deduped) else 0.0

    return {
        "local_rows": local_rows,
        "chi404_rows": chi404_rows,
        "naive_sum_usd": round(naive_sum, 2),
        "deduped_slots": int(len(deduped)),
        "deduped_get_cost_usd": round(deduped_cost, 2),
        "overlap_slots": overlap,
        "duplicate_row_overhead_usd": round(naive_sum - deduped_cost, 2),
        "warning": (
            "naive_sum_usd adds local + chi404 manifest rows (double-counts one account). "
            "deduped_get_cost_usd is one get_cost() per slot — closer, but may still exceed "
            "portal metered GB if estimates overshoot actual streaming usage."
        ),
    }


def _line_items_from_buckets(buckets: dict[tuple[str, str], PortalBucket], *, cost_field: str) -> list[dict]:
    items = []
    for rate_key in sorted(buckets.keys()):
        b = buckets[rate_key]
        if cost_field == "bytes":
            usd = b.usd_from_bytes
            usage_gb = round(b.gb_from_bytes, 4)
        else:
            usd = b.get_cost_usd
            usage_gb = round(b.implied_gb_from_get_cost, 4)
        items.append(
            {
                "label": PORTAL_LINE_LABELS.get(rate_key, f"{b.dataset} / {b.schema}"),
                "dataset": b.dataset,
                "schema": b.schema,
                "usage_gb": usage_gb,
                "rate_usd_per_gb": b.usd_per_gb,
                "data_cost_usd": round(usd, 2),
                "files_or_slots": b.files if cost_field == "bytes" else None,
            }
        )
    return items


def _disk_buckets(repo_root: Path, *, include_chi404: bool) -> tuple[dict[tuple[str, str], PortalBucket], int]:
    seen: set[tuple[str, ...]] = set()
    buckets: dict[tuple[str, str], PortalBucket] = {}
    chi404_only = 0

    def _add(dataset: str, schema: str, key: tuple[str, ...], nbytes: int) -> None:
        if key in seen:
            return
        seen.add(key)
        rate_key = (dataset, schema)
        rate = PORTAL_USD_PER_GB.get(rate_key, 0.0)
        if rate_key not in buckets:
            buckets[rate_key] = PortalBucket(dataset=dataset, schema=schema, usd_per_gb=rate)
        b = buckets[rate_key]
        b.bytes += nbytes
        b.files += 1

    for bf in iter_local_billable_files(repo_root):
        _add(bf.dataset, bf.schema, bf.dedupe_key, bf.bytes)

    if include_chi404:
        for key, nbytes in fetch_chi404_mbo_files().items():
            if key in seen:
                continue
            chi404_only += 1
            dataset, schema, event_id, symbol = key
            _add(dataset, schema, key, nbytes)

    return buckets, chi404_only


def _get_cost_buckets(frames: list) -> dict[tuple[str, str], PortalBucket]:
    deduped = _dedupe_manifest_frames(frames)
    buckets: dict[tuple[str, str], PortalBucket] = {}
    if deduped.empty:
        return buckets
    for _, row in deduped.iterrows():
        ds = str(row.get("dataset", "GLBX.MDP3")).strip()
        schema = str(row.get("schema", "mbo")).strip()
        rate_key = (ds, schema)
        rate = PORTAL_USD_PER_GB.get(rate_key, 0.0)
        if rate_key not in buckets:
            buckets[rate_key] = PortalBucket(dataset=ds, schema=schema, usd_per_gb=rate)
        buckets[rate_key].get_cost_usd += float(row.get("cost", 0.0) or 0.0)
        buckets[rate_key].files += 1
    return buckets


def build_portal_report(
    repo_root: Path,
    *,
    include_chi404: bool = True,
) -> dict[str, Any]:
    repo_root = Path(repo_root)
    frames = []
    for label, path in (
        ("local", repo_root / "data" / "manifest.parquet"),
        ("chi404", repo_root / "runtime/data_downloads/chi404_manifest.parquet"),
    ):
        df = _load_manifest_df(path)
        if df is not None and not df.empty:
            df = df.copy()
            df["_host"] = label
            frames.append(df)

    disk_buckets, chi404_only_files = _disk_buckets(repo_root, include_chi404=include_chi404)
    get_cost_buckets = _get_cost_buckets(frames)

    disk_items = _line_items_from_buckets(disk_buckets, cost_field="bytes")
    get_cost_items = _line_items_from_buckets(get_cost_buckets, cost_field="get_cost")

    disk_subtotal = sum(i["data_cost_usd"] for i in disk_items)
    get_cost_subtotal = sum(i["data_cost_usd"] for i in get_cost_items)
    manifest = manifest_get_cost_summary(repo_root)

    return {
        "billing_model": "portal_gb_times_rate",
        "recommended_estimate": "deduped_get_cost_usd",
        "note": (
            "Databento invoices historical streaming usage (GB x USD/GB), not manifest row counts. "
            "deduped_get_cost_usd = one metadata.get_cost() per unique slot (excludes rebuilt_from_raw "
            "synthetic rows). compressed_disk_usd uses .dbn.zst file bytes and usually UNDERSTATES "
            "portal GB. Compare against the Databento portal for ground truth."
        ),
        "deduped_get_cost": {
            "line_items": get_cost_items,
            "subtotal_usd": round(get_cost_subtotal, 2),
            "implied_total_gb": round(
                sum(i["usage_gb"] for i in get_cost_items if i["usage_gb"]), 4
            ),
        },
        "compressed_disk_bytes": {
            "line_items": disk_items,
            "subtotal_usd": round(disk_subtotal, 2),
            "note": "Underestimates portal — Databento meters decompressed streaming GB, not .zst size.",
            "chi404_only_files": chi404_only_files,
        },
        "manifest_get_cost": manifest,
        "reconciliation": {
            "naive_manifest_sum_usd": manifest["naive_sum_usd"],
            "deduped_get_cost_usd": manifest["deduped_get_cost_usd"],
            "compressed_disk_usd": round(disk_subtotal, 2),
            "naive_minus_deduped_usd": manifest["duplicate_row_overhead_usd"],
            "deduped_minus_disk_usd": round(get_cost_subtotal - disk_subtotal, 2),
            "invoice_benchmark_note": (
                "Compare deduped_get_cost_usd and implied GB against the Databento portal "
                "invoice (e.g. CME MBO 61.22 GB @ $1.80 = $110.19). get_cost can exceed "
                "metered GB when estimates overshoot or duplicate attempts were logged."
            ),
        },
    }
