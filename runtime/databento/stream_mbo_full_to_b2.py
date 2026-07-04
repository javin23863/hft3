"""Resume Databento MBO full history -> B2 (lake/databento_mbo_full).

MBO schema = every order-book event (not sampled). Default weekly chunks keep
each request under Databento's ~5 GB streaming guidance.

B2 layout (weekly, default):
  hft3-b2:Hft3repo/lake/databento_mbo_full[/{subdir}]/{YYYY}/{MM}/{YYYY-MM-DD}.dbn.zst

Phase lanes:
  core12/   — CME futures 9+rates + GC/SI/CL (glbx_core12_symbols.json, GLBX.MDP3)
  extended/ — remaining CME symbols (glbx_extended_symbols.json)
  etf36/    — US ETF MBO (us_etf_mbo_symbols.json, DBEQ.BASIC raw_symbol)

Legacy monthly layout (--chunk month):
  .../{YYYY}/{MM}/GLBX_MDP3_mbo_{YYYY}_{MM}.dbn
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
from hft3_bootstrap import setup_repo_paths  # noqa: E402

setup_repo_paths()

from data_system.src.databento_client import DatabentoResearchClient  # noqa: E402

REMOTE = "hft3-b2:Hft3repo/lake/databento_mbo_full"
SYMBOLS_JSON = Path(__file__).with_name("glbx_continuous_symbols.json")
LEDGER = Path(__file__).with_name("mbo_full_stream_ledger.jsonl")
LOG = Path(__file__).with_name("mbo_full_stream.log")
MIN_BYTES = 4096
MBO_EARLIEST = date(2017, 5, 21)
DATASET_EARLIEST = {
    "GLBX.MDP3": MBO_EARLIEST,
    "DBEQ.BASIC": date(2023, 3, 28),
}
DEFAULT_DATASET = "GLBX.MDP3"


def _rclone() -> str:
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages"
    if winget.is_dir():
        for exe in winget.rglob("rclone.exe"):
            return str(exe)
    return shutil.which("rclone") or "rclone"


def _months(start: date, end: date):
    cur = date(start.year, start.month, 1)
    while cur < end:
        nxt = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
        yield cur, min(nxt, end)
        cur = nxt


def _weeks(start: date, end: date):
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=7), end)
        yield cur, nxt
        cur = nxt


def _remote_root(subdir: str) -> str:
    sub = subdir.strip("/")
    return f"{REMOTE}/{sub}" if sub else REMOTE


def _remote_monthly(remote_root: str, year: int, month: int) -> str:
    return f"{remote_root}/{year}/{month:02d}/GLBX_MDP3_mbo_{year}_{month:02d}.dbn"


def _remote_weekly(remote_root: str, week_start: date) -> str:
    return (
        f"{remote_root}/{week_start.year}/{week_start.month:02d}/"
        f"{week_start.isoformat()}.dbn.zst"
    )


def _remote_bytes(rclone: str, remote: str) -> int | None:
    try:
        out = subprocess.check_output(
            [rclone, "size", remote, "--json"], stderr=subprocess.DEVNULL, text=True
        )
        return int(json.loads(out)["bytes"])
    except Exception:
        return None


def _load_symbols(path: Path, stype: str) -> list[str]:
    if stype == "parent":
        return ["ALL_SYMBOLS"]
    if not path.is_file():
        raise SystemExit(f"Missing symbols file for continuous stype: {path}")
    syms = json.loads(path.read_text(encoding="utf-8"))
    return sorted(set(str(s) for s in syms))


def _ledger(path: Path, entry: dict) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _download_upload(
    client: DatabentoResearchClient,
    rclone: str,
    *,
    tag: str,
    remote: str,
    symbols: list[str],
    start_dt: datetime,
    end_dt: datetime,
    stype_in: str,
    dataset: str,
    ledger: Path,
    symbol_count: int,
    lane: str,
) -> str:
    """Returns status: uploaded | skipped | empty | failed."""
    existing = _remote_bytes(rclone, remote)
    if existing is not None and existing >= MIN_BYTES:
        return "skipped"

    cost = client.estimate_cost(
        symbols, start_dt, end_dt, dataset=dataset, schema="mbo", stype_in=stype_in
    )
    with tempfile.TemporaryDirectory(prefix="mbo_full_") as tmp:
        local = Path(tmp) / f"{tag}.dbn.zst"
        client.download_event_window(
            event_id=f"mbo_{lane}_{tag}",
            symbols=symbols,
            start_utc=start_dt,
            end_utc=end_dt,
            dataset=dataset,
            schema="mbo",
            stype_in=stype_in,
            output_path=str(local),
            override_operating_cap=True,
            override_hard_limit=True,
            cost_estimate=cost,
        )
        nbytes = local.stat().st_size
        if nbytes < MIN_BYTES:
            logging.warning("EMPTY %s bytes=%d", tag, nbytes)
            return "empty"
        subprocess.run(
            [rclone, "copyto", str(local), remote],
            check=True,
            capture_output=True,
            text=True,
        )
        logging.info("OK %s bytes=%d cost=$%.2f -> %s", tag, nbytes, cost, remote)
        _ledger(
            ledger,
            {
                "status": "uploaded",
                "lane": lane,
                "tag": tag,
                "bytes": nbytes,
                "cost_usd": cost,
                "remote": remote,
                "dataset": dataset,
                "stype_in": stype_in,
                "symbols": symbol_count,
            },
        )
        return "uploaded"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=DEFAULT_DATASET)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument(
        "--stype-in",
        default="continuous",
        choices=["continuous", "parent", "raw_symbol"],
    )
    ap.add_argument("--symbols-file", type=Path, default=SYMBOLS_JSON)
    ap.add_argument(
        "--remote-subdir",
        default="",
        help="B2 subfolder under databento_mbo_full (e.g. core12, extended)",
    )
    ap.add_argument("--ledger", type=Path, default=None)
    ap.add_argument("--log-file", type=Path, default=LOG)
    ap.add_argument("--chunk", default="week", choices=["week", "month"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    lane = args.remote_subdir.strip("/") or "full"
    ledger = args.ledger or Path(__file__).with_name(f"mbo_{lane}_stream_ledger.jsonl")
    remote_root = _remote_root(args.remote_subdir)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(args.log_file, encoding="utf-8"),
        ],
    )
    floor = DATASET_EARLIEST.get(args.dataset, MBO_EARLIEST)
    start = max(date.fromisoformat(args.start), floor) if args.start else floor
    end = date.fromisoformat(args.end)
    symbols = _load_symbols(args.symbols_file, args.stype_in)
    rclone = _rclone()
    client = None if args.dry_run else DatabentoResearchClient()

    windows = list(_weeks(start, end) if args.chunk == "week" else _months(start, end))
    logging.info(
        "resume lane=%s dataset=%s remote=%s stype=%s symbols=%d chunk=%s windows=%d range=%s..%s",
        lane,
        args.dataset,
        remote_root,
        args.stype_in,
        len(symbols),
        args.chunk,
        len(windows),
        start,
        end,
    )

    uploaded = skipped = empty = failed = 0
    for w_start, w_end in windows:
        if args.chunk == "week":
            remote = _remote_weekly(remote_root, w_start)
            tag = w_start.isoformat()
        else:
            remote = _remote_monthly(remote_root, w_start.year, w_start.month)
            tag = f"{w_start.year}_{w_start.month:02d}"

        start_dt = datetime.combine(w_start, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = datetime.combine(w_end, datetime.min.time(), tzinfo=timezone.utc)

        if args.dry_run:
            cost = DatabentoResearchClient().estimate_cost(
                symbols,
                start_dt,
                end_dt,
                dataset=args.dataset,
                schema="mbo",
                stype_in=args.stype_in,
            )
            logging.info("DRY %s cost=$%.2f", tag, cost)
            continue

        assert client is not None
        try:
            status = _download_upload(
                client,
                rclone,
                tag=tag,
                remote=remote,
                symbols=symbols,
                start_dt=start_dt,
                end_dt=end_dt,
                stype_in=args.stype_in,
                dataset=args.dataset,
                ledger=ledger,
                symbol_count=len(symbols),
                lane=lane,
            )
            if status == "uploaded":
                uploaded += 1
            elif status == "skipped":
                skipped += 1
            else:
                empty += 1
        except Exception as exc:
            failed += 1
            logging.exception("FAIL %s: %s", tag, exc)

    logging.info(
        "done uploaded=%d skipped=%d empty=%d failed=%d", uploaded, skipped, empty, failed
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
