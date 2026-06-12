"""Unit tests for src/brief.py — offline, no network, no ollama.

Covers:
- Deterministic sections are rendered from fixture data (no model required)
- Model-unavailable path: brief is still written with raw headline bullets
- All four fixed section headings are present in the output
- write_brief() creates the .md file and appends to brief_log.jsonl
- verify_brief() correctly identifies missing headings
- _render_capture_health() produces correct symbol/count lines
- _render_yesterday_label() picks the most recent prior-date record
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch, MagicMock

import pytest

_REPO = Path(__file__).resolve().parents[2]
_APPS = _REPO / "apps"
_PKGS = _REPO / "packages"
for p in (_APPS, _PKGS, str(_REPO)):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from llm_slow_tier.src.config import SlowTierConfig
from llm_slow_tier.src.brief import (
    generate_brief,
    write_brief,
    verify_brief,
    _render_capture_health,
    _render_yesterday_label,
    _render_scheduled_events,
    _format_headlines,
    _REQUIRED_HEADINGS,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_manifest(directory: Path, symbol: str, trade_date: str, **kwargs) -> None:
    defaults = {
        "symbol": symbol,
        "exchange": "CME",
        "trade_date": trade_date,
        "records": 10000,
        "trades": 5000,
        "quotes": 5000,
        "first_ts_exch_ns": 0,
        "last_ts_exch_ns": 1,
        "max_queue_gap_flag_count": 0,
        "md_drops_total": 0,
        "reconnects": 0,
        "file_bytes": 1024,
        "updated_wall_ns": 0,
        "unknown_symbol_drops": 0,
    }
    defaults.update(kwargs)
    (directory / f"{symbol}.manifest.json").write_text(
        json.dumps(defaults), encoding="utf-8"
    )


def _make_label_record(
    trade_date: str,
    label: str = "calm",
    confidence: float = 0.82,
    verdict: str = "accept",
    drivers: List[str] | None = None,
) -> str:
    """Return a JSON-line session label record string."""
    return json.dumps({
        "trade_date": trade_date,
        "label": label,
        "drivers": drivers or ["no notable driver"],
        "confidence": confidence,
        "verifier_verdict": verdict,
        "verifier_reasons": [],
        "model": "gemma4-test",
        "prompt_template_hash": "abc123def456",
        "digest": {},
        "sources_summary": {},
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "advisory": True,
    })


def _gdelt_records() -> List[Dict[str, Any]]:
    return [
        {"title": "Oil markets surge on supply concerns", "domain": "reuters.com", "seendate": "20260612T080000Z"},
        {"title": "Federal Reserve signals rate path", "domain": "ft.com", "seendate": "20260612T090000Z"},
        {"title": "Geopolitical tensions affect gold", "domain": "bloomberg.com", "seendate": "20260612T100000Z"},
    ]


def _make_cfg(tmp_path: Path) -> SlowTierConfig:
    cfg = SlowTierConfig()
    object.__setattr__(cfg, "artifact_root", str(tmp_path / "artifacts"))
    return cfg


# ---------------------------------------------------------------------------
# Tests: verify_brief
# ---------------------------------------------------------------------------

class TestVerifyBrief:

    def test_all_headings_present_returns_empty(self):
        md = (
            "# Morning Brief\n\n"
            "## Overnight drivers\n\nSome prose.\n\n"
            "## Today's scheduled events\n\n- NFP\n\n"
            "## Capture health\n\nAll good.\n\n"
            "## Yesterday's session label\n\nCalm session.\n"
        )
        assert verify_brief(md) == []

    def test_missing_heading_returned(self):
        md = (
            "# Morning Brief\n\n"
            "## Overnight drivers\n\nSome prose.\n\n"
            "## Today's scheduled events\n\n- NFP\n\n"
            "## Capture health\n\nAll good.\n"
            # missing ## Yesterday's session label
        )
        missing = verify_brief(md)
        assert len(missing) == 1
        assert "## Yesterday's session label" in missing[0]

    def test_all_headings_missing_returns_four(self):
        missing = verify_brief("no headings here")
        assert len(missing) == 4

    def test_case_insensitive_match(self):
        md = (
            "## OVERNIGHT DRIVERS\n\n"
            "## TODAY'S SCHEDULED EVENTS\n\n"
            "## CAPTURE HEALTH\n\n"
            "## YESTERDAY'S SESSION LABEL\n"
        )
        assert verify_brief(md) == []


# ---------------------------------------------------------------------------
# Tests: _render_capture_health
# ---------------------------------------------------------------------------

class TestRenderCaptureHealth:

    def test_missing_dir_returns_note(self, tmp_path):
        result = _render_capture_health(tmp_path / "nonexistent")
        assert "No manifests" in result

    def test_empty_dir_returns_note(self, tmp_path):
        d = tmp_path / "manifests"
        d.mkdir()
        result = _render_capture_health(d)
        assert "No valid manifests" in result

    def test_single_symbol(self, tmp_path):
        d = tmp_path / "manifests"
        d.mkdir()
        _make_manifest(d, "MES", "2026-06-12", records=12345, max_queue_gap_flag_count=0)
        result = _render_capture_health(d)
        assert "MES" in result
        assert "12,345" in result
        assert "GAP FLAGS" not in result

    def test_gap_flag_highlighted(self, tmp_path):
        d = tmp_path / "manifests"
        d.mkdir()
        _make_manifest(d, "NQ", "2026-06-12", records=8000, max_queue_gap_flag_count=3)
        result = _render_capture_health(d)
        assert "GAP FLAGS" in result

    def test_multiple_symbols_totals(self, tmp_path):
        d = tmp_path / "manifests"
        d.mkdir()
        _make_manifest(d, "MES", "2026-06-12", records=10000, max_queue_gap_flag_count=0)
        _make_manifest(d, "NQ", "2026-06-12", records=8000, max_queue_gap_flag_count=2)
        result = _render_capture_health(d)
        assert "18,000" in result  # total records
        assert "Gap flags: **2**" in result

    def test_reconnects_shown(self, tmp_path):
        d = tmp_path / "manifests"
        d.mkdir()
        _make_manifest(d, "MES", "2026-06-12", records=1000, reconnects=3)
        result = _render_capture_health(d)
        assert "reconnects=3" in result


# ---------------------------------------------------------------------------
# Tests: _render_yesterday_label
# ---------------------------------------------------------------------------

class TestRenderYesterdayLabel:

    def test_missing_file_returns_note(self, tmp_path):
        result = _render_yesterday_label(tmp_path / "labels.jsonl", "2026-06-12")
        assert "No session labels" in result or "not found" in result.lower()

    def test_picks_last_prior_date(self, tmp_path):
        labels_path = tmp_path / "session_labels.jsonl"
        records = [
            _make_label_record("2026-06-09", label="calm"),
            _make_label_record("2026-06-10", label="macro_event"),
            _make_label_record("2026-06-11", label="war_escalation"),
        ]
        labels_path.write_text("\n".join(records) + "\n", encoding="utf-8")
        # trade_date = 2026-06-12, so last prior is 2026-06-11
        result = _render_yesterday_label(labels_path, "2026-06-12")
        assert "war_escalation" in result
        assert "2026-06-11" in result

    def test_no_prior_record_returns_note(self, tmp_path):
        labels_path = tmp_path / "session_labels.jsonl"
        labels_path.write_text(
            _make_label_record("2026-06-12", label="calm") + "\n",
            encoding="utf-8",
        )
        # trade_date same as only record; no PRIOR records
        result = _render_yesterday_label(labels_path, "2026-06-12")
        assert "No prior session label" in result

    def test_shows_confidence_and_verdict(self, tmp_path):
        labels_path = tmp_path / "session_labels.jsonl"
        labels_path.write_text(
            _make_label_record("2026-06-11", label="calm", confidence=0.91, verdict="accept") + "\n",
            encoding="utf-8",
        )
        result = _render_yesterday_label(labels_path, "2026-06-12")
        assert "0.91" in result
        assert "accept" in result


# ---------------------------------------------------------------------------
# Tests: _render_scheduled_events
# ---------------------------------------------------------------------------

class TestRenderScheduledEvents:

    def test_empty_returns_note(self):
        result = _render_scheduled_events([])
        assert "No scheduled" in result

    def test_events_listed(self):
        result = _render_scheduled_events(["NFP", "CPI"])
        assert "- NFP" in result
        assert "- CPI" in result


# ---------------------------------------------------------------------------
# Tests: generate_brief — model-unavailable path
# ---------------------------------------------------------------------------

class TestGenerateBriefModelUnavailable:
    """When ollama is unavailable, the brief is still written with raw headlines."""

    def _call_with_unavailable_model(self, tmp_path, gdelt_records, gdelt_error=None):
        """Call generate_brief with a mocked ollama that returns an error."""
        cfg = _make_cfg(tmp_path)
        manifest_dir = tmp_path / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        _make_manifest(manifest_dir, "MES", "2026-06-12")

        labels_path = tmp_path / "session_labels.jsonl"
        labels_path.write_text(
            _make_label_record("2026-06-11", label="calm") + "\n",
            encoding="utf-8",
        )

        # Patch ollama_call to simulate unavailable model
        with patch("llm_slow_tier.src.brief.ollama_call") as mock_call:
            mock_call.return_value = (None, "model not found in ollama list")
            brief_text, model_used = generate_brief(
                trade_date="2026-06-12",
                gdelt_records=gdelt_records,
                gdelt_error=gdelt_error,
                calendar_hits=["NFP"],
                manifest_dir=manifest_dir,
                labels_path=labels_path,
                model="gemma4-test",
                cfg=cfg,
            )
        return brief_text, model_used

    def test_model_unavailable_brief_still_written(self, tmp_path):
        brief_text, model_used = self._call_with_unavailable_model(
            tmp_path, _gdelt_records()
        )
        assert isinstance(brief_text, str)
        assert len(brief_text) > 0
        assert model_used is False

    def test_model_unavailable_has_all_headings(self, tmp_path):
        brief_text, _ = self._call_with_unavailable_model(tmp_path, _gdelt_records())
        missing = verify_brief(brief_text)
        assert missing == [], f"Missing headings: {missing}"

    def test_model_unavailable_raw_headlines_present(self, tmp_path):
        brief_text, _ = self._call_with_unavailable_model(tmp_path, _gdelt_records())
        assert "model unavailable" in brief_text.lower()
        assert "Oil markets surge" in brief_text

    def test_gdelt_error_produces_offline_notice(self, tmp_path):
        # No gdelt records AND gdelt_error set: should not call model
        brief_text, model_used = self._call_with_unavailable_model(
            tmp_path,
            gdelt_records=[],
            gdelt_error="gdelt_error: HTTP 503",
        )
        # The brief should have a notice and model_used=False (we didn't even try)
        assert "model unavailable" in brief_text.lower()
        assert model_used is False


# ---------------------------------------------------------------------------
# Tests: generate_brief — model available path
# ---------------------------------------------------------------------------

class TestGenerateBriefModelAvailable:

    def test_model_prose_appears_in_overnight_section(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        manifest_dir = tmp_path / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        _make_manifest(manifest_dir, "MES", "2026-06-12")

        labels_path = tmp_path / "session_labels.jsonl"
        labels_path.write_text(
            _make_label_record("2026-06-11", label="macro_event") + "\n",
            encoding="utf-8",
        )

        model_prose = "Markets were driven overnight by oil supply tensions and a hawkish Fed signal."
        with patch("llm_slow_tier.src.brief.ollama_call") as mock_call:
            mock_call.return_value = (model_prose, None)
            brief_text, model_used = generate_brief(
                trade_date="2026-06-12",
                gdelt_records=_gdelt_records(),
                gdelt_error=None,
                calendar_hits=["CPI"],
                manifest_dir=manifest_dir,
                labels_path=labels_path,
                model="gemma4-test",
                cfg=cfg,
            )

        assert model_used is True
        assert model_prose in brief_text
        missing = verify_brief(brief_text)
        assert missing == []

    def test_deterministic_sections_not_from_model(self, tmp_path):
        """Code-rendered sections must NOT be re-generated by the model."""
        cfg = _make_cfg(tmp_path)
        manifest_dir = tmp_path / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        _make_manifest(manifest_dir, "MES", "2026-06-12", records=9999)

        labels_path = tmp_path / "session_labels.jsonl"
        labels_path.write_text(
            _make_label_record("2026-06-11", label="calm", confidence=0.75) + "\n",
            encoding="utf-8",
        )

        with patch("llm_slow_tier.src.brief.ollama_call") as mock_call:
            mock_call.return_value = ("Some overnight prose from model.", None)
            brief_text, model_used = generate_brief(
                trade_date="2026-06-12",
                gdelt_records=_gdelt_records(),
                gdelt_error=None,
                calendar_hits=["NFP", "CPI"],
                manifest_dir=manifest_dir,
                labels_path=labels_path,
                model="gemma4-test",
                cfg=cfg,
            )

        # Deterministic section content from data
        assert "NFP" in brief_text
        assert "CPI" in brief_text
        assert "MES" in brief_text
        assert "9,999" in brief_text
        assert "0.75" in brief_text
        assert "calm" in brief_text


# ---------------------------------------------------------------------------
# Tests: write_brief
# ---------------------------------------------------------------------------

class TestWriteBrief:

    def test_creates_md_file(self, tmp_path):
        artifact_root = str(tmp_path / "artifacts")
        brief_text = (
            "# Morning Brief — 2026-06-12\n\n"
            "## Overnight drivers\n\nProse.\n\n"
            "## Today's scheduled events\n\n- NFP\n\n"
            "## Capture health\n\nGood.\n\n"
            "## Yesterday's session label\n\nCalm.\n"
        )
        brief_path, log_path = write_brief(
            brief_text=brief_text,
            trade_date="2026-06-12",
            artifact_root=artifact_root,
            model="gemma4-test",
            gdelt_n=3,
            calendar_n=1,
            model_used=True,
        )
        assert brief_path.exists()
        assert brief_path.name == "morning_brief_2026-06-12.md"
        assert brief_path.read_text(encoding="utf-8") == brief_text

    def test_appends_log_record(self, tmp_path):
        artifact_root = str(tmp_path / "artifacts")
        write_brief(
            brief_text="# test\n## Overnight drivers\n## Today's scheduled events\n"
                       "## Capture health\n## Yesterday's session label\n",
            trade_date="2026-06-12",
            artifact_root=artifact_root,
            model="gemma4-test",
            gdelt_n=5,
            calendar_n=2,
            model_used=False,
        )
        log_path = Path(artifact_root) / "brief_log.jsonl"
        assert log_path.exists()
        lines = [l for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["date"] == "2026-06-12"
        assert rec["gdelt_n"] == 5
        assert rec["calendar_n"] == 2
        assert rec["model_used"] is False
        assert rec["model"] == "gemma4-test"
        assert "generated_utc" in rec

    def test_log_appends_on_second_call(self, tmp_path):
        artifact_root = str(tmp_path / "artifacts")
        for date in ("2026-06-11", "2026-06-12"):
            write_brief(
                brief_text=f"# {date}\n## Overnight drivers\n## Today's scheduled events\n"
                           "## Capture health\n## Yesterday's session label\n",
                trade_date=date,
                artifact_root=artifact_root,
                model="gemma4-test",
                gdelt_n=1,
                calendar_n=0,
                model_used=True,
            )
        log_path = Path(artifact_root) / "brief_log.jsonl"
        lines = [l for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 2
