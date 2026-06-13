"""Thin decode layer: DBN definition-schema files -> instrument_id -> OptionMeta map.

Verified against databento_dbn 0.79.0 (installed package):
  InstrumentDefMsg key fields used here:
    instrument_id    (int)   Databento internal ID for the instrument.
    instrument_class (InstrumentClass enum)
                             CALL == 'C', PUT == 'P', OPTION_SPREAD == 'T';
                             spreads and non-options are excluded.
    strike_price     (int64) Raw fixed-point strike; divide by FIXED_PRICE_SCALE
                             (1_000_000_000) to get USD float.  The sentinel value
                             INT64_MAX (9223372036854775807) marks non-strike legs
                             (spreads) and MUST be excluded.
    expiration       (int64) UTC nanoseconds; convert via
                             datetime.fromtimestamp(ns/1e9, tz=UTC).date().
    underlying       (str)   Front-month underlying symbol, e.g. 'ESM3'.
    asset            (str)   Root asset, e.g. 'ES' or 'EW3'.
    raw_symbol       (str)   Human-readable symbol, e.g. 'EW3K3 C4750'.

  FIXED_PRICE_SCALE = 1_000_000_000.

  Definition .dbn.zst files are PER-DAY dissemination (NOT uniform full snapshots).
  A mid-week file may hold only that day's changes.  Therefore the
  instrument_id->meta map MUST be built CUMULATIVELY across all definition files
  in a directory (rglob), with the latest definition per instrument_id winning.
  In practice a monthly file already contains repeated records for all active
  instruments; deduplication (latest-wins) collapses them to unique instruments.

  Validated against C:/hft3-lake/options/definitions/pm/EW3/EW3_def_2023-05.dbn.zst:
    - 609 unique instruments expire 2023-05-19 after cumulative latest-wins dedup.
    - Sample raw_symbols: 'EW3K3 C4140', 'EW3K3 C4750', etc.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Union

try:
    import databento
    import databento_dbn
except ImportError:  # pragma: no cover — only missing in bare envs
    databento = None  # type: ignore[assignment]
    databento_dbn = None  # type: ignore[assignment]

# INT64_MAX sentinel used by Databento for missing/non-applicable fixed-point fields.
_INT64_MAX = 9_223_372_036_854_775_807
_FIXED_PRICE_SCALE = 1_000_000_000
_UTC = datetime.timezone.utc


# ---------------------------------------------------------------------------
# Public data structure
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OptionMeta:
    """Lightweight option instrument descriptor decoded from a DBN definition record.

    Attributes
    ----------
    expiration : datetime.date
        Option expiry date (UTC); derived from InstrumentDefMsg.expiration (ns).
    strike : float or None
        Strike price in USD.  None is not produced by the current filter logic
        (sentinel-strike records are excluded), but the type is kept open for
        forward compatibility with synthetic fixtures.
    right : Literal['C', 'P']
        'C' for call, 'P' for put.
    underlying : str
        Front-month underlying symbol, e.g. 'ESM3'.
    root_asset : str
        Root asset string from InstrumentDefMsg.asset, e.g. 'EW3'.
    raw_symbol : str
        Human-readable symbol string, e.g. 'EW3K3 C4750'.
    """

    expiration: datetime.date
    strike: float | None
    right: str           # 'C' | 'P'
    underlying: str
    root_asset: str
    raw_symbol: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_definition_map(
    dir_or_paths: Union[str, Path, list[Union[str, Path]]],
) -> dict[int, OptionMeta]:
    """Build an instrument_id -> OptionMeta map from DBN definition files.

    Files are processed in path-sort order; the *last* definition record seen
    for each instrument_id wins (handles per-day delta files — later files
    supersede earlier ones for the same instrument).

    Parameters
    ----------
    dir_or_paths : str | Path | list[str | Path]
        Either a directory (all ``*.dbn.zst`` files found recursively via
        ``rglob`` are processed, sorted by path), or an explicit list of file
        paths (processed in list order).

    Returns
    -------
    dict[int, OptionMeta]
        Maps Databento ``instrument_id`` to its decoded option metadata.
        Only CALL ('C') and PUT ('P') instruments with non-sentinel strikes are
        included.  Spreads (OPTION_SPREAD 'T') and any other instrument classes
        are silently excluded.

    Raises
    ------
    ImportError
        If ``databento`` is not installed.
    FileNotFoundError
        If a specified file path does not exist.
    """
    if databento is None or databento_dbn is None:
        raise ImportError("databento package required: pip install databento")

    _CALL = databento_dbn.InstrumentClass.CALL
    _PUT = databento_dbn.InstrumentClass.PUT

    paths = _resolve_paths(dir_or_paths)

    result: dict[int, OptionMeta] = {}

    for path in paths:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"DBN definition file not found: {path}")

        store = databento.DBNStore.from_file(str(path))
        for rec in store:
            # Only process InstrumentDefMsg records (rtype 'instrument-def')
            if not hasattr(rec, "instrument_class"):
                continue

            ic = rec.instrument_class
            if ic not in (_CALL, _PUT):
                continue  # drop spreads and non-options

            strike_raw = rec.strike_price
            if strike_raw == _INT64_MAX:
                continue  # sentinel — not a plain option strike

            # Decode fields
            expiration = datetime.datetime.fromtimestamp(
                rec.expiration / 1e9, tz=_UTC
            ).date()
            strike = float(strike_raw) / _FIXED_PRICE_SCALE
            right = "C" if ic == _CALL else "P"

            result[rec.instrument_id] = OptionMeta(
                expiration=expiration,
                strike=strike,
                right=right,
                underlying=rec.underlying,
                root_asset=rec.asset,
                raw_symbol=rec.raw_symbol,
            )

    return result


def instruments_expiring_on(
    def_map: dict[int, OptionMeta],
    d: datetime.date,
) -> list[int]:
    """Return instrument_ids whose expiration date equals ``d``.

    Parameters
    ----------
    def_map : dict[int, OptionMeta]
        Result of :func:`load_definition_map`.
    d : datetime.date
        Target expiry date.

    Returns
    -------
    list[int]
        Unsorted list of matching instrument_ids.
    """
    return [iid for iid, meta in def_map.items() if meta.expiration == d]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_paths(
    dir_or_paths: Union[str, Path, list[Union[str, Path]]],
) -> list[Path]:
    """Normalise the dir_or_paths argument to a sorted list of Path objects."""
    if isinstance(dir_or_paths, (str, Path)):
        base = Path(dir_or_paths)
        if base.is_dir():
            return sorted(base.rglob("*.dbn.zst"))
        # Single file path passed as string/Path
        return [base]
    # List of paths
    return [Path(p) for p in dir_or_paths]
