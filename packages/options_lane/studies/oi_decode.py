"""Thin decode layer: DBN statistics-schema -> open-interest numpy arrays.

Verified against databento_dbn 0.79.0 (2026-06-13):
  StatType.OPEN_INTEREST == 9
  StatUpdateAction.NEW   == 1   (keep)
  StatUpdateAction.DELETE == 2  (discard)

StatMsg field semantics for OPEN_INTEREST records:
  quantity     -- OI CONTRACT COUNT (int64). This is the datum to extract.
  price        -- INT64_MAX sentinel (9223372036854775807). MUST be ignored for OI.
  ts_ref       -- reference timestamp (UTC nanoseconds) = the session/trade date the
                  stat applies to. Convert to date via:
                      datetime.fromtimestamp(ts_ref / 1e9, tz=UTC).date()
                  Use ts_ref (not ts_event) for the session date key.
  update_action -- 1=NEW (keep), 2=DELETE (discard). A later NEW for the same
                  (instrument_id, ts_ref) supersedes the earlier one (last-wins).
  instrument_id -- numeric instrument identifier, matches InstrumentDefMsg.instrument_id.

Definition files (InstrumentDefMsg) are PER-DAY DISSEMINATION — a mid-week file may
only carry that day's changes. Build the instrument_id -> metadata map cumulatively
across all definition files in a directory (rglob); latest definition per
instrument_id wins.

InstrumentDefMsg fields of interest:
  instrument_id     -- numeric key
  expiration        -- ns UTC -> date (INT64_MAX sentinel = no expiry)
  strike_price      -- raw int / FIXED_PRICE_SCALE = USD strike; INT64_MAX = spread/non-strike
  instrument_class  -- databento_dbn.InstrumentClass: CALL='C', PUT='P', OPTION_SPREAD='T'
  underlying        -- e.g. 'ESM3'
  raw_symbol        -- e.g. 'EW3K3 C4750'
  asset             -- e.g. 'ES'

FIXED_PRICE_SCALE = 1_000_000_000

See also: dbn_trades.py (aggressor-sign field semantics), dbn_ohlcv.py (bar timestamps).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_UTC = timezone.utc
_OI_STAT_TYPE = 9    # StatType.OPEN_INTEREST
_NEW_ACTION   = 1    # StatUpdateAction.NEW


def _ns_to_date(ts_ns: int):
    """Convert UTC nanoseconds to a datetime.date (UTC calendar date)."""
    return datetime.fromtimestamp(ts_ns / 1e9, tz=_UTC).date()


def _decode_oi_records(store) -> tuple[list, list, list]:
    """Iterate a DBNStore and collect OI NEW records.

    Returns (ts_ref_dates, instrument_ids, quantities) as plain Python lists.
    Deduplication (last-wins) is done by the caller after full iteration so that
    the in-order stream semantics are preserved within a single file.
    """
    ts_ref_dates: list = []
    instrument_ids: list[int] = []
    quantities: list[int] = []

    for rec in store:
        # stat_type and update_action can be int or enum depending on dbn version;
        # compare the int value to avoid enum import brittleness.
        try:
            st = int(rec.stat_type)
        except AttributeError:
            continue
        if st != _OI_STAT_TYPE:
            continue
        try:
            ua = int(rec.update_action)
        except AttributeError:
            continue
        if ua != _NEW_ACTION:
            continue

        ts_ref_dates.append(_ns_to_date(rec.ts_ref))
        instrument_ids.append(int(rec.instrument_id))
        quantities.append(int(rec.quantity))

    return ts_ref_dates, instrument_ids, quantities


def _apply_last_wins(
    ts_ref_dates: list,
    instrument_ids: list[int],
    quantities: list[int],
) -> dict[str, np.ndarray]:
    """Deduplicate by (instrument_id, ts_ref_date), last occurrence wins.

    Returns the standard OI arrays dict.
    """
    # Iterate in order; later entries for the same key overwrite earlier ones.
    seen: dict[tuple, int] = {}  # (instrument_id, ts_ref_date) -> index into final lists
    final_dates: list = []
    final_ids: list[int] = []
    final_ois: list[int] = []

    for d, iid, q in zip(ts_ref_dates, instrument_ids, quantities):
        key = (iid, d)
        if key in seen:
            idx = seen[key]
            final_dates[idx] = d
            final_ids[idx] = iid
            final_ois[idx] = q
        else:
            seen[key] = len(final_dates)
            final_dates.append(d)
            final_ids.append(iid)
            final_ois.append(q)

    n = len(final_dates)
    if n == 0:
        return {
            "ts_ref_date":   np.empty(0, dtype=object),
            "instrument_id": np.empty(0, dtype=np.int64),
            "oi":            np.empty(0, dtype=np.int64),
        }

    return {
        "ts_ref_date":   np.array(final_dates, dtype=object),
        "instrument_id": np.array(final_ids,   dtype=np.int64),
        "oi":            np.array(final_ois,   dtype=np.int64),
    }


def _empty_oi_dict() -> dict[str, np.ndarray]:
    return {
        "ts_ref_date":   np.empty(0, dtype=object),
        "instrument_id": np.empty(0, dtype=np.int64),
        "oi":            np.empty(0, dtype=np.int64),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_oi_from_dbn(path: str | Path) -> dict[str, np.ndarray]:
    """Load open-interest records from a single DBN statistics .dbn.zst file.

    Filters to stat_type == OPEN_INTEREST (9) AND update_action == NEW (1).
    The OI count is taken from the ``quantity`` field; the ``price`` field
    contains an INT64_MAX sentinel for OI records and is ignored.

    For the session date key, ``ts_ref`` (not ``ts_event``) is used:
        datetime.fromtimestamp(ts_ref / 1e9, tz=UTC).date()

    When two records share (instrument_id, ts_ref_date), the last one read wins
    (mirrors real Databento update semantics where a later NEW supersedes earlier).

    Parameters
    ----------
    path : str or Path
        Path to a .dbn or .dbn.zst statistics-schema file.

    Returns
    -------
    dict with keys:
        ts_ref_date   (object ndarray of datetime.date) session date from ts_ref
        instrument_id (int64)  Databento numeric instrument identifier
        oi            (int64)  open-interest contract count (from ``quantity``)

    Returns empty arrays (length 0) when the file has no OI/NEW records.

    Raises
    ------
    ImportError       if databento is not installed.
    FileNotFoundError if path does not exist.
    """
    try:
        import databento
    except ImportError as exc:
        raise ImportError("databento package required: pip install databento") from exc

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"DBN file not found: {path}")

    store = databento.DBNStore.from_file(str(path))
    ts_ref_dates, instrument_ids, quantities = _decode_oi_records(store)

    return _apply_last_wins(ts_ref_dates, instrument_ids, quantities)


def load_oi_dir(dir_path: str | Path) -> dict[str, np.ndarray]:
    """Load open-interest records from all .dbn.zst files under a directory.

    Recursively finds every ``*.dbn.zst`` file under ``dir_path`` (rglob) and
    concatenates the decoded OI arrays. Deduplication (last-wins) is applied
    globally across all files after concatenation, so the order files are
    processed in does NOT affect the final result — each unique
    (instrument_id, ts_ref_date) pair keeps the record from whichever file was
    processed last.

    This is appropriate for statistics directories organised as monthly chunks:
    all chunks are loaded together to form one flat OI table.

    Parameters
    ----------
    dir_path : str or Path
        Root directory containing one or more .dbn.zst statistics files.

    Returns
    -------
    Same dict shape as load_oi_from_dbn.
    Returns empty arrays when dir_path is missing, empty, or has no OI records.
    """
    dir_path = Path(dir_path)
    if not dir_path.exists():
        return _empty_oi_dict()

    all_dates: list = []
    all_ids: list[int] = []
    all_ois: list[int] = []

    files = sorted(dir_path.rglob("*.dbn.zst"))
    if not files:
        return _empty_oi_dict()

    try:
        import databento
    except ImportError as exc:
        raise ImportError("databento package required: pip install databento") from exc

    for fpath in files:
        store = databento.DBNStore.from_file(str(fpath))
        d_list, i_list, q_list = _decode_oi_records(store)
        all_dates.extend(d_list)
        all_ids.extend(i_list)
        all_ois.extend(q_list)

    if not all_dates:
        return _empty_oi_dict()

    return _apply_last_wins(all_dates, all_ids, all_ois)
