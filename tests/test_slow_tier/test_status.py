"""Unit tests for src/status.py — offline, no network, no ollama.

Covers:
- Clean window (no problems) → prints "OK", exit 0, no file written
- Missing label: weekday with manifests but no session label → problem fired
- Missing label: weekday without manifests → NOT a problem
- Conflict-review in window → problem fired
- Conflict-review outside window → NOT a problem
- Capture gap_flags > 0 → problem fired
- Capture md_drops > 0 → problem fired
- Capture reconnects > 2 → problem fired
- Capture unknown_symbol_drops > 0 → problem fired
- Stale capture: most-recent manifest > 2 days old → problem fired
- Fresh capture (same day) → NOT a problem
- GDELT persistent failure in last 3 sessions → problem fired
- GDELT failure in only 2 of 3 sessions → NOT a problem
- Review queue non-empty → problem fired
- Review queue absent → NOT a problem
- Problems file shape: problems_latest.json has required fields
- Problems history appended
- Multiple problems accumulate into single run_status call
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

_REPO = Path(__file__).resolve().parents[2]
_APPS = _REPO / "apps"
_PKGS = _REPO / "packages"
for p in (_APPS, _PKGS, str(_REPO)):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from llm_slow_tier.src.config import SlowTierConfig
from llm_slow_tier.src.status import (
    check_capture_problems,
    check_conflict_reviews,
    check_gdelt_persistent_failure,
    check_missing_labels,
    check_review_queue,
    check_stale_capture,
    run_status,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path: Path) -> SlowTierConfig:
    return SlowTierConfig(
        artifact_root=str(tmp_path / "slow_tier"),
        model="test-model",
    )


def _write_manifest(directory: Path, symbol: str, trade_date: str, **kwargs) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    defaults = {
        "symbol": symbol,
        "exchange": "CME",
        "trade_date": trade_date,
        "records": 1000,
        "trades": 500,
        "quotes": 500,
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
    (directory / f"{symbol}.manifest.json").write_text(json.dumps(defaults), encoding="utf-8")


def _write_session_label(labels_path: Path, trade_date: str, verdict: str = "accept", gdelt_error: Optional[str] = None) -> None:
    labels_path.parent.mkdir(parents=True, exist_ok=True)
    sources_summary: Dict[str, Any] = {
        "calendar_hits": [],
        "gdelt_top_events": [],
        "gdelt_error": gdelt_error,
        "offline": False,
    }
    rec: Dict[str, Any] = {
        "trade_date": trade_date,
        "label": "calm" if verdict == "accept" else "conflict_review",
        "verifier_verdict": verdict,
        "verifier_reasons": ["tape_contradicts_calm"] if verdict == "conflict_review" else [],
        "drivers": [],
        "confidence": 0.8,
        "model": "test-model",
        "sources_summary": sources_summary,
    }
    with open(labels_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def _write_review_queue_entry(queue_path: Path, trade_date: str) -> None:
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"date": trade_date, "model_label": "calm", "verifier_reasons": ["x"], "generated_utc": "2026-06-10T00:00:00+00:00"}
    with open(queue_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Tests: check_missing_labels
# ---------------------------------------------------------------------------

class TestCheckMissingLabels:

    def test_weekday_with_manifests_no_label(self, tmp_path):
        manifest_root = tmp_path / "manifests"
        date_str = "2026-06-09"  # Monday
        day_dir = manifest_root / date_str
        _write_manifest(day_dir, "MES", date_str)

        labels_by_date: Dict[str, Any] = {}  # no labels at all
        window = date.fromisoformat(date_str)
        problems = check_missing_labels(window, window, manifest_root, labels_by_date)

        assert len(problems) == 1
        assert "missing_label" in problems[0]
        assert date_str in problems[0]

    def test_weekday_with_manifests_and_label_no_problem(self, tmp_path):
        manifest_root = tmp_path / "manifests"
        date_str = "2026-06-09"
        day_dir = manifest_root / date_str
        _write_manifest(day_dir, "MES", date_str)

        labels_by_date = {date_str: {"trade_date": date_str}}
        window = date.fromisoformat(date_str)
        problems = check_missing_labels(window, window, manifest_root, labels_by_date)

        assert problems == []

    def test_weekday_no_manifests_no_problem(self, tmp_path):
        """Missing label is only a problem if manifests exist for that day."""
        manifest_root = tmp_path / "manifests"
        date_str = "2026-06-09"
        # No manifest directory for this date
        labels_by_date: Dict[str, Any] = {}
        window = date.fromisoformat(date_str)
        problems = check_missing_labels(window, window, manifest_root, labels_by_date)

        assert problems == []

    def test_weekend_excluded(self, tmp_path):
        """Saturday/Sunday are skipped even if manifests exist."""
        manifest_root = tmp_path / "manifests"
        sat = "2026-06-13"  # Saturday
        sun = "2026-06-14"  # Sunday
        for d in [sat, sun]:
            day_dir = manifest_root / d
            _write_manifest(day_dir, "MES", d)
        labels_by_date: Dict[str, Any] = {}
        start = date.fromisoformat(sat)
        end = date.fromisoformat(sun)
        problems = check_missing_labels(start, end, manifest_root, labels_by_date)
        assert problems == []

    def test_multiple_missing_days(self, tmp_path):
        manifest_root = tmp_path / "manifests"
        missing_dates = ["2026-06-09", "2026-06-10"]
        for d in missing_dates:
            _write_manifest(manifest_root / d, "MES", d)
        labels_by_date: Dict[str, Any] = {}
        start = date.fromisoformat(missing_dates[0])
        end = date.fromisoformat(missing_dates[-1])
        problems = check_missing_labels(start, end, manifest_root, labels_by_date)
        assert len(problems) == 2


# ---------------------------------------------------------------------------
# Tests: check_conflict_reviews
# ---------------------------------------------------------------------------

class TestCheckConflictReviews:

    def _make_record(self, trade_date: str, verdict: str, reasons: Optional[List[str]] = None) -> Dict[str, Any]:
        return {
            "trade_date": trade_date,
            "verifier_verdict": verdict,
            "verifier_reasons": reasons or [],
        }

    def test_conflict_review_in_window(self):
        records = [self._make_record("2026-06-10", "conflict_review", ["tape_contradicts_calm"])]
        window = date.fromisoformat("2026-06-10")
        problems = check_conflict_reviews(window, window, records)
        assert len(problems) == 1
        assert "conflict_review" in problems[0]
        assert "2026-06-10" in problems[0]

    def test_accept_verdict_no_problem(self):
        records = [self._make_record("2026-06-10", "accept")]
        window = date.fromisoformat("2026-06-10")
        problems = check_conflict_reviews(window, window, records)
        assert problems == []

    def test_conflict_review_outside_window_not_a_problem(self):
        records = [self._make_record("2026-06-01", "conflict_review")]
        start = date.fromisoformat("2026-06-09")
        end = date.fromisoformat("2026-06-12")
        problems = check_conflict_reviews(start, end, records)
        assert problems == []

    def test_multiple_conflict_reviews(self):
        records = [
            self._make_record("2026-06-09", "conflict_review"),
            self._make_record("2026-06-10", "accept"),
            self._make_record("2026-06-11", "conflict_review"),
        ]
        start = date.fromisoformat("2026-06-09")
        end = date.fromisoformat("2026-06-11")
        problems = check_conflict_reviews(start, end, records)
        assert len(problems) == 2


# ---------------------------------------------------------------------------
# Tests: check_capture_problems
# ---------------------------------------------------------------------------

class TestCheckCaptureProblems:

    def test_gap_flags_fires(self, tmp_path):
        manifest_root = tmp_path / "manifests"
        date_str = "2026-06-10"
        _write_manifest(manifest_root / date_str, "MES", date_str, max_queue_gap_flag_count=5)
        problems = check_capture_problems(manifest_root)
        assert any("capture_gap_flags" in p for p in problems)

    def test_md_drops_fires(self, tmp_path):
        manifest_root = tmp_path / "manifests"
        date_str = "2026-06-10"
        _write_manifest(manifest_root / date_str, "MES", date_str, md_drops_total=1)
        problems = check_capture_problems(manifest_root)
        assert any("capture_md_drops" in p for p in problems)

    def test_reconnects_above_2_fires(self, tmp_path):
        manifest_root = tmp_path / "manifests"
        date_str = "2026-06-10"
        _write_manifest(manifest_root / date_str, "MES", date_str, reconnects=3)
        problems = check_capture_problems(manifest_root)
        assert any("capture_reconnects" in p for p in problems)

    def test_reconnects_exactly_2_no_problem(self, tmp_path):
        manifest_root = tmp_path / "manifests"
        date_str = "2026-06-10"
        _write_manifest(manifest_root / date_str, "MES", date_str, reconnects=2)
        problems = check_capture_problems(manifest_root)
        assert not any("capture_reconnects" in p for p in problems)

    def test_unknown_symbol_drops_fires(self, tmp_path):
        manifest_root = tmp_path / "manifests"
        date_str = "2026-06-10"
        _write_manifest(manifest_root / date_str, "MES", date_str, unknown_symbol_drops=2)
        problems = check_capture_problems(manifest_root)
        assert any("capture_unknown_drops" in p for p in problems)

    def test_clean_manifest_no_problems(self, tmp_path):
        manifest_root = tmp_path / "manifests"
        date_str = "2026-06-10"
        _write_manifest(manifest_root / date_str, "MES", date_str)  # all defaults = 0
        problems = check_capture_problems(manifest_root)
        assert problems == []

    def test_no_manifest_root_no_problems(self, tmp_path):
        manifest_root = tmp_path / "nonexistent"
        problems = check_capture_problems(manifest_root)
        assert problems == []

    def test_uses_most_recent_dir(self, tmp_path):
        """Only the most-recently-dated dir is checked; older bad data is ignored."""
        manifest_root = tmp_path / "manifests"
        # Old dir: has problem
        _write_manifest(manifest_root / "2026-06-08", "MES", "2026-06-08", max_queue_gap_flag_count=10)
        # Recent dir: clean
        _write_manifest(manifest_root / "2026-06-10", "MES", "2026-06-10")
        problems = check_capture_problems(manifest_root)
        assert problems == []


# ---------------------------------------------------------------------------
# Tests: check_stale_capture
# ---------------------------------------------------------------------------

class TestCheckStaleCapture:

    def test_stale_3_days_fires(self, tmp_path):
        manifest_root = tmp_path / "manifests"
        today = date(2026, 6, 12)
        old_dir = manifest_root / "2026-06-09"
        old_dir.mkdir(parents=True, exist_ok=True)
        problems = check_stale_capture(manifest_root, today)
        assert len(problems) == 1
        assert "stale_capture" in problems[0]

    def test_same_day_no_problem(self, tmp_path):
        manifest_root = tmp_path / "manifests"
        today = date(2026, 6, 12)
        fresh_dir = manifest_root / "2026-06-12"
        fresh_dir.mkdir(parents=True, exist_ok=True)
        problems = check_stale_capture(manifest_root, today)
        assert problems == []

    def test_exactly_2_days_no_problem(self, tmp_path):
        manifest_root = tmp_path / "manifests"
        today = date(2026, 6, 12)
        dir_2days = manifest_root / "2026-06-10"
        dir_2days.mkdir(parents=True, exist_ok=True)
        problems = check_stale_capture(manifest_root, today)
        assert problems == []

    def test_no_manifest_dirs_no_problem(self, tmp_path):
        manifest_root = tmp_path / "manifests"
        today = date(2026, 6, 12)
        problems = check_stale_capture(manifest_root, today)
        assert problems == []


# ---------------------------------------------------------------------------
# Tests: check_gdelt_persistent_failure
# ---------------------------------------------------------------------------

class TestCheckGdeltPersistentFailure:

    def _make_record(self, trade_date: str, gdelt_error: Optional[str]) -> Dict[str, Any]:
        return {
            "trade_date": trade_date,
            "sources_summary": {"gdelt_error": gdelt_error},
        }

    def test_all_last_3_have_error_fires(self):
        records = [
            self._make_record("2026-06-08", "timeout"),
            self._make_record("2026-06-09", "timeout"),
            self._make_record("2026-06-10", "timeout"),
        ]
        problems = check_gdelt_persistent_failure(records)
        assert len(problems) == 1
        assert "gdelt_persistent_failure" in problems[0]

    def test_2_of_3_have_error_no_problem(self):
        records = [
            self._make_record("2026-06-08", "timeout"),
            self._make_record("2026-06-09", None),
            self._make_record("2026-06-10", "timeout"),
        ]
        problems = check_gdelt_persistent_failure(records)
        assert problems == []

    def test_fewer_than_3_records_no_problem(self):
        records = [
            self._make_record("2026-06-09", "timeout"),
            self._make_record("2026-06-10", "timeout"),
        ]
        problems = check_gdelt_persistent_failure(records)
        assert problems == []

    def test_no_records_no_problem(self):
        assert check_gdelt_persistent_failure([]) == []

    def test_uses_only_last_3_records(self):
        """Earlier records with errors do not matter if last 3 are clean."""
        records = [
            self._make_record("2026-06-06", "timeout"),
            self._make_record("2026-06-07", "timeout"),
            self._make_record("2026-06-08", "timeout"),
            self._make_record("2026-06-09", None),   # last 3 start here
            self._make_record("2026-06-10", None),
            self._make_record("2026-06-11", None),
        ]
        problems = check_gdelt_persistent_failure(records)
        assert problems == []


# ---------------------------------------------------------------------------
# Tests: check_review_queue
# ---------------------------------------------------------------------------

class TestCheckReviewQueue:

    def test_non_empty_queue_fires(self, tmp_path):
        golden_dir = tmp_path / "golden"
        queue_path = golden_dir / "review_queue.jsonl"
        _write_review_queue_entry(queue_path, "2026-06-10")

        problems = check_review_queue(golden_dir)
        assert len(problems) == 1
        assert "review_queue" in problems[0]
        assert "1" in problems[0]

    def test_absent_queue_no_problem(self, tmp_path):
        golden_dir = tmp_path / "golden"
        golden_dir.mkdir(parents=True, exist_ok=True)
        problems = check_review_queue(golden_dir)
        assert problems == []

    def test_count_reported_correctly(self, tmp_path):
        golden_dir = tmp_path / "golden"
        queue_path = golden_dir / "review_queue.jsonl"
        for d in ["2026-06-08", "2026-06-09", "2026-06-10"]:
            _write_review_queue_entry(queue_path, d)
        problems = check_review_queue(golden_dir)
        assert "3" in problems[0]


# ---------------------------------------------------------------------------
# Tests: run_status — end-to-end
# ---------------------------------------------------------------------------

class TestRunStatus:

    def test_clean_window_returns_ok_exit0_no_file(self, tmp_path, monkeypatch):
        """No problems → exit 0, no problems file written."""
        cfg = _make_cfg(tmp_path)
        # Create a recent clean manifest (today)
        today = datetime.now(timezone.utc).date()
        manifest_root = Path(cfg.artifact_root) / cfg.manifest_subdir
        _write_manifest(manifest_root / today.isoformat(), "MES", today.isoformat())
        # Session label for today
        labels_path = Path(cfg.artifact_root) / "session_labels.jsonl"
        _write_session_label(labels_path, today.isoformat(), "accept")
        # No review queue

        monkeypatch.chdir(tmp_path)
        problems, exit_code = run_status(cfg, days=1)

        assert exit_code == 0
        assert problems == []
        assert not (tmp_path / "runtime" / "slow_tier" / "problems_latest.json").exists()

    def test_missing_label_fires(self, tmp_path, monkeypatch):
        """Today is a weekday with manifests but no label → problem."""
        cfg = _make_cfg(tmp_path)
        today = datetime.now(timezone.utc).date()
        # Only use a known-weekday date for this test
        # Find the most recent Monday
        days_since_mon = today.weekday()
        monday = today - timedelta(days=days_since_mon)

        manifest_root = Path(cfg.artifact_root) / cfg.manifest_subdir
        _write_manifest(manifest_root / monday.isoformat(), "MES", monday.isoformat())
        # No session labels

        monkeypatch.chdir(tmp_path)
        problems, exit_code = run_status(cfg, days=7)

        assert exit_code == 1
        assert any("missing_label" in p for p in problems)

    def test_conflict_review_fires(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        today = datetime.now(timezone.utc).date()
        labels_path = Path(cfg.artifact_root) / "session_labels.jsonl"
        _write_session_label(labels_path, today.isoformat(), verdict="conflict_review")

        monkeypatch.chdir(tmp_path)
        problems, exit_code = run_status(cfg, days=7)

        assert exit_code == 1
        assert any("conflict_review" in p for p in problems)

    def test_problems_file_written_on_failure(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        today = datetime.now(timezone.utc).date()
        labels_path = Path(cfg.artifact_root) / "session_labels.jsonl"
        _write_session_label(labels_path, today.isoformat(), verdict="conflict_review")

        monkeypatch.chdir(tmp_path)
        problems, exit_code = run_status(cfg, days=7)

        assert exit_code == 1
        latest = tmp_path / "runtime" / "slow_tier" / "problems_latest.json"
        assert latest.is_file()
        data = json.loads(latest.read_text(encoding="utf-8"))
        assert "generated_utc" in data
        assert "n_problems" in data
        assert "problems" in data
        assert data["n_problems"] == len(problems)
        assert isinstance(data["problems"], list)

    def test_problems_history_appended(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        today = datetime.now(timezone.utc).date()
        labels_path = Path(cfg.artifact_root) / "session_labels.jsonl"

        monkeypatch.chdir(tmp_path)

        for _ in range(3):
            _write_session_label(labels_path, today.isoformat(), verdict="conflict_review")
            run_status(cfg, days=7)

        history = tmp_path / "runtime" / "slow_tier" / "problems_history.jsonl"
        assert history.is_file()
        entries = [json.loads(l) for l in history.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(entries) == 3

    def test_stale_capture_fires(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        manifest_root = Path(cfg.artifact_root) / cfg.manifest_subdir
        old_dir = manifest_root / "2020-01-01"
        old_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.chdir(tmp_path)
        problems, exit_code = run_status(cfg, days=7)

        assert exit_code == 1
        assert any("stale_capture" in p for p in problems)

    def test_gdelt_persistent_failure_fires(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        labels_path = Path(cfg.artifact_root) / "session_labels.jsonl"
        # Write 3 records all with gdelt_error
        for d in ["2026-06-08", "2026-06-09", "2026-06-10"]:
            labels_path.parent.mkdir(parents=True, exist_ok=True)
            rec = {
                "trade_date": d,
                "label": "calm",
                "verifier_verdict": "accept",
                "verifier_reasons": [],
                "drivers": [],
                "confidence": 0.8,
                "model": "test",
                "sources_summary": {
                    "calendar_hits": [],
                    "gdelt_top_events": [],
                    "gdelt_error": "connection_timeout",
                    "offline": False,
                },
            }
            with open(labels_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")

        monkeypatch.chdir(tmp_path)
        problems, exit_code = run_status(cfg, days=7)

        assert any("gdelt_persistent_failure" in p for p in problems)

    def test_review_queue_fires(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        golden_dir = Path(cfg.artifact_root) / cfg.golden_subdir
        queue_path = golden_dir / "review_queue.jsonl"
        _write_review_queue_entry(queue_path, "2026-06-10")

        monkeypatch.chdir(tmp_path)
        problems, exit_code = run_status(cfg, days=7)

        assert exit_code == 1
        assert any("review_queue" in p for p in problems)
