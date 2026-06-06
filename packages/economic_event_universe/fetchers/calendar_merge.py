"""Merge FRED bootstrap rows with agency fetch rows — agency wins on conflict."""

from __future__ import annotations

from typing import Iterable


def _key(row: dict) -> tuple[str, str]:
    return (str(row["event_type"]), str(row["release_date"]))


def merge_calendar_rows(
    *sources: Iterable[dict],
    agency_first: bool = True,
) -> list[dict]:
    """Later sources overwrite earlier for same (event_type, release_date) when agency_first=False.

    When agency_first=True (default), pass agency rows last so they win.
    """
    by_key: dict[tuple[str, str], dict] = {}
    order = sources if agency_first else reversed(sources)
    for rows in order:
        for row in rows:
            by_key[_key(row)] = dict(row)
    return sorted(by_key.values(), key=lambda r: (r["event_type"], r["release_date"]))


def dedupe_rows(rows: Iterable[dict]) -> list[dict]:
    return merge_calendar_rows(rows, agency_first=True)
