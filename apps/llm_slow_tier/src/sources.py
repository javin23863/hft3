"""Source data fetchers for the LLM slow-tier lane.

(a) Release-calendar lookup: which scheduled events fall on a given date.
    Reads the CSV files under packages/data_system/config/release_calendars/
    and maps event types via packages/economic_event_universe/config/event_universe.yaml.

(b) GDELT day summary: top N article records via the GDELT DOC 2.0 article-search
    API (https://api.gdeltproject.org/api/v2/doc/doc).  Network errors produce an
    empty list and an error note.  Offline mode skips the fetch entirely.

Neither function has side-effects beyond reading local files or making a single
outbound HTTP request.
"""

from __future__ import annotations

import csv
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import SlowTierConfig


# ---------------------------------------------------------------------------
# Paths (relative to repo root, resolved at call time)
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    """Return the repository root (parent of apps/, packages/, etc.)."""
    return Path(__file__).resolve().parents[3]


def _release_calendars_dir() -> Path:
    return _repo_root() / "packages" / "data_system" / "config" / "release_calendars"


def _event_universe_yaml() -> Path:
    return _repo_root() / "packages" / "economic_event_universe" / "config" / "event_universe.yaml"


# ---------------------------------------------------------------------------
# Release-calendar lookup
# ---------------------------------------------------------------------------

def _load_event_universe_names() -> Dict[str, str]:
    """Return mapping {event_type_key -> display_name} from event_universe.yaml.

    Falls back to an empty dict if PyYAML is absent or the file is missing.
    """
    path = _event_universe_yaml()
    if not path.is_file():
        return {}
    try:
        import yaml
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        events: Dict[str, Any] = raw.get("events") or {}
        # Keys are event type names like "CPI", "NFP", etc.
        return {k: k for k in events}
    except Exception:
        return {}


def load_calendar_hits(trade_date: str, _cfg: SlowTierConfig) -> List[str]:
    """Return a list of event-type labels that fall on trade_date.

    Scans every CSV in the release_calendars directory.  The CSV column
    ``release_date`` is compared to trade_date (YYYY-MM-DD).  The column
    ``event_type`` names the event.  Returns sorted, deduplicated list.
    """
    calendar_dir = _release_calendars_dir()
    if not calendar_dir.is_dir():
        return []

    hits: set[str] = set()

    for csv_path in sorted(calendar_dir.glob("*.csv")):
        try:
            with csv_path.open(newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    if row.get("release_date", "").strip() == trade_date:
                        event_type = row.get("event_type", "").strip()
                        if event_type:
                            hits.add(event_type)
        except (OSError, csv.Error):
            continue

    return sorted(hits)


# ---------------------------------------------------------------------------
# GDELT DOC 2.0 article-search summary
# ---------------------------------------------------------------------------

_GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

_DEFAULT_GDELT_QUERY = (
    "(oil OR gold OR crude OR opec OR iran OR fed OR inflation OR tariff)"
    " sourcelang:eng"
)


def _gdelt_datetime(trade_date: str, end: bool = False) -> str:
    """Convert YYYY-MM-DD to GDELT datetime string YYYYMMDDHHMMSS.

    start -> 000000, end -> 235959.
    """
    date_nodash = trade_date.replace("-", "")
    suffix = "235959" if end else "000000"
    return date_nodash + suffix


def _compress_gdelt_article(article: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the fields the labeler prompt needs from a GDELT DOC article dict."""
    return {
        "title": (article.get("title") or "").strip(),
        "domain": article.get("domain") or "",
        "seendate": article.get("seendate") or "",
    }


def load_gdelt_summary(
    trade_date: str,
    cfg: SlowTierConfig,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Fetch top N GDELT article records for trade_date via the DOC 2.0 API.

    Calls:
      GET https://api.gdeltproject.org/api/v2/doc/doc
          ?query=<terms>&mode=artlist&format=json
          &maxrecords=<N>&startdatetime=YYYYMMDDHHMMSS&enddatetime=YYYYMMDDHHMMSS

    Returns (articles, error_note).  On any network or parse failure returns
    ([], "gdelt_error: <detail>").  Deduplicates on case-insensitive exact title
    match after stripping whitespace.
    """
    query_terms = cfg.gdelt.query
    max_records = cfg.gdelt.max_records

    params = urllib.parse.urlencode({
        "query": query_terms,
        "mode": "artlist",
        "format": "json",
        "maxrecords": str(max_records),
        "startdatetime": _gdelt_datetime(trade_date, end=False),
        "enddatetime": _gdelt_datetime(trade_date, end=True),
    })
    url = f"{_GDELT_DOC_API}?{params}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hft3-slow-tier/1.0"})
        with urllib.request.urlopen(req, timeout=cfg.gdelt.timeout_s) as resp:
            raw_bytes = resp.read()
    except urllib.error.HTTPError as exc:
        return [], f"gdelt_error: HTTP {exc.code} {exc.reason}"
    except urllib.error.URLError as exc:
        return [], f"gdelt_error: {exc.reason}"
    except Exception as exc:
        return [], f"gdelt_error: {exc}"

    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return [], f"gdelt_error: JSON parse failed: {exc}"

    articles_raw: List[Dict[str, Any]] = payload.get("articles") or []

    # Compress and deduplicate (case-insensitive exact title match)
    seen_titles: set[str] = set()
    compressed: List[Dict[str, Any]] = []
    for art in articles_raw:
        item = _compress_gdelt_article(art)
        title_key = item["title"].lower()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        compressed.append(item)
        if len(compressed) >= max_records:
            break

    return compressed, None
