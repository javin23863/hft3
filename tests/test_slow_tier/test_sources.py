"""Unit tests for src/sources.py — offline, no network.

Covers:
- GDELT DOC 2.0 API: parse a canned JSON fixture -> correct article list
- GDELT DOC 2.0 API: deduplication of near-identical titles (case-insensitive)
- GDELT DOC 2.0 API: max_records cap honoured
- GDELT error (HTTP error) -> empty list + note starting "gdelt_error:"
- GDELT error (URL error) -> empty list + note starting "gdelt_error:"
- GDELT error (bad JSON) -> empty list + note starting "gdelt_error:"
- Offline mode: load_gdelt_summary is not called (tested at call site; here we
  confirm the function itself works in isolation when given a valid response)
"""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).resolve().parents[2]
_APPS = _REPO / "apps"
_PKGS = _REPO / "packages"
for p in (_APPS, _PKGS, str(_REPO)):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from llm_slow_tier.src.config import GdeltConfig, SlowTierConfig
from llm_slow_tier.src.sources import (
    _compress_gdelt_article,
    _gdelt_datetime,
    load_gdelt_summary,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_cfg(max_records: int = 5, timeout_s: float = 10.0) -> SlowTierConfig:
    cfg = SlowTierConfig()
    object.__setattr__(
        cfg,
        "gdelt",
        GdeltConfig(max_records=max_records, timeout_s=timeout_s),
    )
    return cfg


def _canned_response(articles: list[dict]) -> bytes:
    """Encode an article list as the bytes the DOC 2.0 API would return."""
    return json.dumps({"articles": articles}).encode("utf-8")


def _mock_urlopen(body_bytes: bytes):
    """Return a context-manager mock that yields a response reading body_bytes."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.read = MagicMock(return_value=body_bytes)
    return cm


SAMPLE_ARTICLES = [
    {
        "title": "Oil prices surge on OPEC supply cut",
        "url": "https://example.com/oil-1",
        "seendate": "20260610T120000Z",
        "domain": "example.com",
        "sourcecountry": "US",
    },
    {
        "title": "Fed signals rate pause amid inflation concerns",
        "url": "https://example.com/fed-1",
        "seendate": "20260610T130000Z",
        "domain": "reuters.com",
        "sourcecountry": "US",
    },
    {
        "title": "Gold hits record high on safe-haven demand",
        "url": "https://example.com/gold-1",
        "seendate": "20260610T140000Z",
        "domain": "bloomberg.com",
        "sourcecountry": "US",
    },
]


# ---------------------------------------------------------------------------
# _gdelt_datetime helper
# ---------------------------------------------------------------------------

class TestGdeltDatetime:

    def test_start_of_day(self):
        assert _gdelt_datetime("2026-06-10", end=False) == "20260610000000"

    def test_end_of_day(self):
        assert _gdelt_datetime("2026-06-10", end=True) == "20260610235959"


# ---------------------------------------------------------------------------
# _compress_gdelt_article helper
# ---------------------------------------------------------------------------

class TestCompressGdeltArticle:

    def test_extracts_title_domain_seendate(self):
        art = {
            "title": "  Fed raises rates  ",
            "domain": "reuters.com",
            "seendate": "20260610T120000Z",
            "url": "https://reuters.com/article",
            "sourcecountry": "US",
        }
        result = _compress_gdelt_article(art)
        assert result == {
            "title": "Fed raises rates",
            "domain": "reuters.com",
            "seendate": "20260610T120000Z",
        }

    def test_missing_fields_return_empty_strings(self):
        result = _compress_gdelt_article({})
        assert result["title"] == ""
        assert result["domain"] == ""
        assert result["seendate"] == ""


# ---------------------------------------------------------------------------
# load_gdelt_summary — success path
# ---------------------------------------------------------------------------

class TestLoadGdeltSummarySuccess:

    def test_parses_canned_fixture(self):
        """Full DOC 2.0 response parsed to title/domain/seendate dicts."""
        cfg = _make_cfg(max_records=10)
        body = _canned_response(SAMPLE_ARTICLES)

        with patch("urllib.request.urlopen", return_value=_mock_urlopen(body)):
            records, error = load_gdelt_summary("2026-06-10", cfg)

        assert error is None
        assert len(records) == 3
        assert records[0]["title"] == "Oil prices surge on OPEC supply cut"
        assert records[0]["domain"] == "example.com"
        assert records[0]["seendate"] == "20260610T120000Z"
        assert records[1]["title"] == "Fed signals rate pause amid inflation concerns"
        assert records[2]["title"] == "Gold hits record high on safe-haven demand"

    def test_result_keys_are_title_domain_seendate_only(self):
        """Each returned dict must have exactly the three labeler-facing keys."""
        cfg = _make_cfg(max_records=10)
        body = _canned_response(SAMPLE_ARTICLES)

        with patch("urllib.request.urlopen", return_value=_mock_urlopen(body)):
            records, _ = load_gdelt_summary("2026-06-10", cfg)

        for rec in records:
            assert set(rec.keys()) == {"title", "domain", "seendate"}

    def test_max_records_cap(self):
        """Response is truncated to cfg.gdelt.max_records."""
        cfg = _make_cfg(max_records=2)
        body = _canned_response(SAMPLE_ARTICLES)  # 3 articles

        with patch("urllib.request.urlopen", return_value=_mock_urlopen(body)):
            records, error = load_gdelt_summary("2026-06-10", cfg)

        assert error is None
        assert len(records) == 2

    def test_deduplication_case_insensitive(self):
        """Duplicate titles (differing only in case/whitespace) are dropped."""
        articles = [
            {"title": "Oil Surge", "domain": "a.com", "seendate": "20260610T100000Z"},
            {"title": "oil surge", "domain": "b.com", "seendate": "20260610T110000Z"},  # dup
            {"title": "  OIL SURGE  ", "domain": "c.com", "seendate": "20260610T120000Z"},  # dup
            {"title": "Gold rises", "domain": "d.com", "seendate": "20260610T130000Z"},
        ]
        cfg = _make_cfg(max_records=10)
        body = _canned_response(articles)

        with patch("urllib.request.urlopen", return_value=_mock_urlopen(body)):
            records, error = load_gdelt_summary("2026-06-10", cfg)

        assert error is None
        # Should have 2 unique titles: "Oil Surge" and "Gold rises"
        assert len(records) == 2
        assert records[0]["title"] == "Oil Surge"
        assert records[1]["title"] == "Gold rises"

    def test_empty_articles_array(self):
        """API returns articles:[] -> empty list, no error."""
        cfg = _make_cfg(max_records=10)
        body = _canned_response([])

        with patch("urllib.request.urlopen", return_value=_mock_urlopen(body)):
            records, error = load_gdelt_summary("2026-06-10", cfg)

        assert error is None
        assert records == []

    def test_missing_articles_key(self):
        """API returns JSON without 'articles' key -> empty list, no error."""
        cfg = _make_cfg(max_records=10)
        body = json.dumps({}).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=_mock_urlopen(body)):
            records, error = load_gdelt_summary("2026-06-10", cfg)

        assert error is None
        assert records == []


# ---------------------------------------------------------------------------
# load_gdelt_summary — error paths
# ---------------------------------------------------------------------------

class TestLoadGdeltSummaryErrors:

    def test_http_error_returns_empty_list_and_gdelt_error_note(self):
        """HTTP 404 -> ([], 'gdelt_error: ...')."""
        cfg = _make_cfg()
        http_error = urllib.error.HTTPError(
            url="http://x", code=404, msg="Not Found", hdrs=None, fp=None
        )

        with patch("urllib.request.urlopen", side_effect=http_error):
            records, error = load_gdelt_summary("2026-06-10", cfg)

        assert records == []
        assert error is not None
        assert error.startswith("gdelt_error:")

    def test_url_error_returns_empty_list_and_gdelt_error_note(self):
        """Connection failure -> ([], 'gdelt_error: ...')."""
        cfg = _make_cfg()
        url_error = urllib.error.URLError(reason="Connection refused")

        with patch("urllib.request.urlopen", side_effect=url_error):
            records, error = load_gdelt_summary("2026-06-10", cfg)

        assert records == []
        assert error is not None
        assert error.startswith("gdelt_error:")

    def test_timeout_returns_empty_list_and_gdelt_error_note(self):
        """Timeout (generic exception) -> ([], 'gdelt_error: ...')."""
        import socket
        cfg = _make_cfg()

        with patch("urllib.request.urlopen", side_effect=socket.timeout("timed out")):
            records, error = load_gdelt_summary("2026-06-10", cfg)

        assert records == []
        assert error is not None
        assert error.startswith("gdelt_error:")

    def test_bad_json_returns_empty_list_and_gdelt_error_note(self):
        """Response is not valid JSON -> ([], 'gdelt_error: ...')."""
        cfg = _make_cfg()
        bad_body_cm = _mock_urlopen(b"this is not json {{{")

        with patch("urllib.request.urlopen", return_value=bad_body_cm):
            records, error = load_gdelt_summary("2026-06-10", cfg)

        assert records == []
        assert error is not None
        assert error.startswith("gdelt_error:")
