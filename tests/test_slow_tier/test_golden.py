"""Unit tests for src/golden.py — offline, no network, no ollama.

Covers:
- load_golden_set: reads all valid {date}.json files
- load_golden_set: skips malformed files
- agreement computation: 100% agreement
- agreement computation: 0% agreement
- agreement computation: partial agreement
- conflict_rate computation
- gate_pass = True when agreement >= 0.90 AND conflict_rate < 0.10
- gate_pass = False when agreement < 0.90
- gate_pass = False when conflict_rate >= 0.10
- gate_pass = False when n_golden == 0
- eval artifact written to runtime/validation/slow_tier_eval.json
- per_date entries have required fields
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_APPS = _REPO / "apps"
_PKGS = _REPO / "packages"
for p in (_APPS, _PKGS, str(_REPO)):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from llm_slow_tier.src.config import SlowTierConfig
from llm_slow_tier.src.golden import load_golden_set, run_eval


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_config(tmp_path: Path) -> SlowTierConfig:
    cfg = SlowTierConfig()
    # Override artifact_root so session_labels.jsonl and eval output are in tmp_path
    cfg = SlowTierConfig(
        artifact_root=str(tmp_path / "slow_tier"),
        model="test-model",
    )
    return cfg


def _write_golden(golden_dir: Path, date: str, label: str, notes: str = "") -> None:
    golden_dir.mkdir(parents=True, exist_ok=True)
    path = golden_dir / f"{date}.json"
    path.write_text(json.dumps({"date": date, "label": label, "notes": notes}), encoding="utf-8")


def _write_session_label(labels_path: Path, trade_date: str, label: str, verdict: str) -> None:
    labels_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "trade_date": trade_date,
        "label": label,
        "verifier_verdict": verdict,
        "drivers": [],
        "confidence": 0.8,
        "model": "test-model",
    }
    with open(labels_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Tests: load_golden_set
# ---------------------------------------------------------------------------

class TestLoadGoldenSet:

    def test_loads_valid_files(self, tmp_path):
        golden_dir = tmp_path / "golden"
        _write_golden(golden_dir, "2026-06-01", "calm")
        _write_golden(golden_dir, "2026-06-02", "macro_event", "NFP day")

        entries = load_golden_set(golden_dir)
        assert len(entries) == 2
        dates = {e["date"] for e in entries}
        assert "2026-06-01" in dates
        assert "2026-06-02" in dates

    def test_skips_malformed_json(self, tmp_path):
        golden_dir = tmp_path / "golden"
        golden_dir.mkdir(parents=True)
        (golden_dir / "bad.json").write_text("not json", encoding="utf-8")
        _write_golden(golden_dir, "2026-06-01", "calm")

        entries = load_golden_set(golden_dir)
        assert len(entries) == 1

    def test_skips_missing_date_or_label(self, tmp_path):
        golden_dir = tmp_path / "golden"
        golden_dir.mkdir(parents=True)
        (golden_dir / "no_date.json").write_text(json.dumps({"label": "calm"}), encoding="utf-8")
        (golden_dir / "no_label.json").write_text(json.dumps({"date": "2026-06-01"}), encoding="utf-8")

        entries = load_golden_set(golden_dir)
        assert len(entries) == 0

    def test_empty_directory(self, tmp_path):
        golden_dir = tmp_path / "golden"
        golden_dir.mkdir(parents=True)
        assert load_golden_set(golden_dir) == []

    def test_nonexistent_directory(self, tmp_path):
        assert load_golden_set(tmp_path / "nonexistent") == []


# ---------------------------------------------------------------------------
# Tests: run_eval — agreement and conflict rate
# ---------------------------------------------------------------------------

class TestRunEval:

    def test_full_agreement_gate_pass(self, tmp_path):
        """All labels match golden -> agreement=1.0, gate passes if conflict_rate < 0.10."""
        cfg = _make_config(tmp_path)
        golden_dir = Path(cfg.artifact_root) / "golden"
        labels_path = Path(cfg.artifact_root) / "session_labels.jsonl"

        dates = [f"2026-06-{i:02d}" for i in range(1, 11)]
        for date in dates:
            _write_golden(golden_dir, date, "calm")
            _write_session_label(labels_path, date, "calm", "accept")

        result = run_eval(golden_dir, cfg)

        assert result["n_golden"] == 10
        assert result["agreement"] == pytest.approx(1.0)
        assert result["conflict_rate"] == pytest.approx(0.0)
        assert result["gate_pass"] is True

    def test_zero_agreement_gate_fails(self, tmp_path):
        """All labels disagree -> agreement=0.0 -> gate_pass=False."""
        cfg = _make_config(tmp_path)
        golden_dir = Path(cfg.artifact_root) / "golden"
        labels_path = Path(cfg.artifact_root) / "session_labels.jsonl"

        dates = [f"2026-06-{i:02d}" for i in range(1, 11)]
        for date in dates:
            _write_golden(golden_dir, date, "calm")
            _write_session_label(labels_path, date, "macro_event", "accept")

        result = run_eval(golden_dir, cfg)

        assert result["agreement"] == pytest.approx(0.0)
        assert result["gate_pass"] is False

    def test_partial_agreement_below_gate_threshold(self, tmp_path):
        """80% agreement -> gate_pass=False."""
        cfg = _make_config(tmp_path)
        golden_dir = Path(cfg.artifact_root) / "golden"
        labels_path = Path(cfg.artifact_root) / "session_labels.jsonl"

        for i in range(1, 9):  # 8 agree
            date = f"2026-06-{i:02d}"
            _write_golden(golden_dir, date, "calm")
            _write_session_label(labels_path, date, "calm", "accept")
        for i in range(9, 11):  # 2 disagree
            date = f"2026-06-{i:02d}"
            _write_golden(golden_dir, date, "calm")
            _write_session_label(labels_path, date, "macro_event", "accept")

        result = run_eval(golden_dir, cfg)

        assert result["n_golden"] == 10
        assert result["agreement"] == pytest.approx(0.8)
        assert result["gate_pass"] is False

    def test_high_conflict_rate_gate_fails(self, tmp_path):
        """Agreement >= 0.90 but conflict_rate >= 0.10 -> gate_pass=False."""
        cfg = _make_config(tmp_path)
        golden_dir = Path(cfg.artifact_root) / "golden"
        labels_path = Path(cfg.artifact_root) / "session_labels.jsonl"

        # 10 dates: 10 agree (agreement=1.0) but 2 have conflict_review verdict
        for i in range(1, 9):
            date = f"2026-06-{i:02d}"
            _write_golden(golden_dir, date, "calm")
            _write_session_label(labels_path, date, "calm", "accept")
        for i in range(9, 11):
            date = f"2026-06-{i:02d}"
            _write_golden(golden_dir, date, "conflict_review")
            _write_session_label(labels_path, date, "conflict_review", "conflict_review")

        result = run_eval(golden_dir, cfg)

        assert result["conflict_rate"] == pytest.approx(0.2)
        assert result["gate_pass"] is False

    def test_empty_golden_set_gate_fails(self, tmp_path):
        """No golden entries -> n_golden=0 -> agreement=0.0 -> gate_pass=False."""
        cfg = _make_config(tmp_path)
        golden_dir = Path(cfg.artifact_root) / "golden"
        golden_dir.mkdir(parents=True, exist_ok=True)

        result = run_eval(golden_dir, cfg)

        assert result["n_golden"] == 0
        assert result["gate_pass"] is False

    def test_missing_stored_label_counts_as_disagree(self, tmp_path):
        """Golden date with no stored label -> agree=False."""
        cfg = _make_config(tmp_path)
        golden_dir = Path(cfg.artifact_root) / "golden"
        _write_golden(golden_dir, "2026-06-01", "calm")
        # No session_labels.jsonl at all

        result = run_eval(golden_dir, cfg)

        assert result["n_golden"] == 1
        assert result["agreement"] == pytest.approx(0.0)
        assert result["gate_pass"] is False

    def test_per_date_required_fields(self, tmp_path):
        """per_date entries have all required fields."""
        cfg = _make_config(tmp_path)
        golden_dir = Path(cfg.artifact_root) / "golden"
        labels_path = Path(cfg.artifact_root) / "session_labels.jsonl"

        _write_golden(golden_dir, "2026-06-01", "calm")
        _write_session_label(labels_path, "2026-06-01", "calm", "accept")

        result = run_eval(golden_dir, cfg)

        required = {"date", "golden_label", "model_label", "final_label", "verifier_verdict", "agree"}
        for entry in result["per_date"]:
            missing = required - set(entry.keys())
            assert not missing, f"per_date entry missing fields {missing}: {entry}"

    def test_eval_artifact_written(self, tmp_path, monkeypatch):
        """run_eval writes runtime/validation/slow_tier_eval.json."""
        cfg = _make_config(tmp_path)
        golden_dir = Path(cfg.artifact_root) / "golden"
        golden_dir.mkdir(parents=True, exist_ok=True)

        # Change working directory so relative path 'runtime/...' lands in tmp_path
        monkeypatch.chdir(tmp_path)

        result = run_eval(golden_dir, cfg)

        eval_path = tmp_path / "runtime" / "validation" / "slow_tier_eval.json"
        assert eval_path.is_file(), "slow_tier_eval.json was not written"
        written = json.loads(eval_path.read_text(encoding="utf-8"))
        assert "gate_pass" in written
        assert "generated_utc" in written
        assert "per_date" in written

    def test_gate_exactly_at_threshold_passes(self, tmp_path):
        """Exactly 90% agreement AND exactly 9% conflict -> gate_pass=True."""
        cfg = _make_config(tmp_path)
        golden_dir = Path(cfg.artifact_root) / "golden"
        labels_path = Path(cfg.artifact_root) / "session_labels.jsonl"

        # 10 dates: 9 agree (90%), 0 conflict_review -> gate passes
        for i in range(1, 10):
            date = f"2026-06-{i:02d}"
            _write_golden(golden_dir, date, "calm")
            _write_session_label(labels_path, date, "calm", "accept")
        _write_golden(golden_dir, "2026-06-10", "calm")
        _write_session_label(labels_path, "2026-06-10", "macro_event", "accept")

        result = run_eval(golden_dir, cfg)
        assert result["agreement"] == pytest.approx(0.9)
        assert result["conflict_rate"] == pytest.approx(0.0)
        assert result["gate_pass"] is True
