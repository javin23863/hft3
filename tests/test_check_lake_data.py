"""Smoke tests for scripts/check_lake_data report shape (Phase 0)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "check_lake_data.py"


def _load_check_lake_data():
    spec = importlib.util.spec_from_file_location("check_lake_data", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


_EXPECTED_KEYS = frozenset(
    {
        "valid_unit_ids",
        "invalid_unit_ids",
        "checked_paths",
        "checked_at",
        "source",
        "valid_count",
        "invalid_count",
        "invalid_reasons",
        "scan_progress",
    }
)


def test_scan_lake_data_valid_unit_report_shape(tmp_path: Path, monkeypatch) -> None:
    mod = _load_check_lake_data()
    units_path = tmp_path / "units.jsonl"
    units_path.write_text(
        '{"unit_id":"u_ok","symbol":"MES","event_id":"E1"}\n',
        encoding="utf-8",
    )

    def _ok_row(row, repo_root, symbols):
        uid = str(row["unit_id"])
        return uid, "ok", str(tmp_path / f"{uid}.npz")

    monkeypatch.setattr(mod, "_check_unit_row", _ok_row)
    report = mod.scan_lake_data(
        repo_root=tmp_path,
        units_jsonl=units_path,
        manifest_only=False,
        symbols=("MES",),
    )
    assert set(report.keys()) == _EXPECTED_KEYS
    assert report["valid_count"] == 1
    assert report["invalid_count"] == 0
    assert report["valid_unit_ids"] == ["u_ok"]
    assert report["invalid_unit_ids"] == {}
    assert report["source"] == str(units_path)


def test_scan_lake_data_invalid_unit_report_shape(tmp_path: Path, monkeypatch) -> None:
    mod = _load_check_lake_data()
    units_path = tmp_path / "units.jsonl"
    units_path.write_text(
        '{"unit_id":"u_bad","symbol":"MES","event_id":"E2"}\n',
        encoding="utf-8",
    )

    def _bad_row(row, repo_root, symbols):
        uid = str(row["unit_id"])
        return uid, "insufficient_events", str(tmp_path / f"{uid}.npz")

    monkeypatch.setattr(mod, "_check_unit_row", _bad_row)
    report = mod.scan_lake_data(
        repo_root=tmp_path,
        units_jsonl=units_path,
        manifest_only=False,
        symbols=("MES",),
    )
    assert set(report.keys()) == _EXPECTED_KEYS
    assert report["valid_count"] == 0
    assert report["invalid_count"] == 1
    assert report["valid_unit_ids"] == []
    assert report["invalid_unit_ids"] == {"u_bad": "insufficient_events"}


def test_scan_lake_data_requires_exactly_one_source(tmp_path: Path) -> None:
    mod = _load_check_lake_data()
    with pytest.raises(ValueError, match="Provide --units-jsonl or --manifest-only"):
        mod.scan_lake_data(
            repo_root=tmp_path,
            units_jsonl=None,
            manifest_only=False,
            symbols=("MES",),
        )


def test_scan_lake_data_offset_and_max_units(tmp_path: Path, monkeypatch) -> None:
    mod = _load_check_lake_data()
    units_path = tmp_path / "units.jsonl"
    units_path.write_text(
        "".join(
            f'{{"unit_id":"u{i}","symbol":"MES","event_id":"E{i}"}}\n'
            for i in range(5)
        ),
        encoding="utf-8",
    )

    def _ok_row(row, repo_root, symbols):
        uid = str(row["unit_id"])
        return uid, "ok", str(tmp_path / f"{uid}.npz")

    monkeypatch.setattr(mod, "_check_unit_row", _ok_row)
    report = mod.scan_lake_data(
        repo_root=tmp_path,
        units_jsonl=units_path,
        manifest_only=False,
        symbols=("MES",),
        offset=2,
        max_units=2,
        total_source_rows=5,
    )
    assert report["valid_count"] == 2
    assert report["valid_unit_ids"] == ["u2", "u3"]
    assert report["scan_progress"]["next_offset"] == 4
    assert report["scan_progress"]["complete"] is False


def test_build_summary_report_counts_only(tmp_path: Path) -> None:
    mod = _load_check_lake_data()
    report = {
        "valid_count": 2,
        "invalid_count": 1,
        "invalid_reasons": {"missing_npz": 1},
        "source": "units.jsonl",
        "checked_at": "2026-06-26T00:00:00+00:00",
        "scan_progress": {"complete": False, "next_offset": 3},
        "valid_unit_ids": ["a", "b"],
        "invalid_unit_ids": {"c": "missing_npz"},
    }
    summary = mod.build_summary_report(report)
    assert "valid_unit_ids" not in summary
    assert summary["valid_count"] == 2
    assert summary["invalid_reasons"] == {"missing_npz": 1}


def test_scan_lake_data_blank_lines_chunked_resume(tmp_path: Path, monkeypatch) -> None:
    mod = _load_check_lake_data()
    units_path = tmp_path / "units.jsonl"
    units_path.write_text(
        '{"unit_id":"u0","symbol":"MES","event_id":"E0"}\n'
        "\n"
        '{"unit_id":"u1","symbol":"MES","event_id":"E1"}\n'
        '{"unit_id":"u2","symbol":"MES","event_id":"E2"}\n',
        encoding="utf-8",
    )

    def _ok_row(row, repo_root, symbols):
        uid = str(row["unit_id"])
        return uid, "ok", str(tmp_path / f"{uid}.npz")

    monkeypatch.setattr(mod, "_check_unit_row", _ok_row)
    first = mod.scan_lake_data(
        repo_root=tmp_path,
        units_jsonl=units_path,
        manifest_only=False,
        symbols=("MES",),
        max_units=1,
    )
    assert first["valid_count"] == 1
    assert first["scan_progress"]["next_offset"] == 1
    assert first["scan_progress"]["complete"] is False

    second = mod.scan_lake_data(
        repo_root=tmp_path,
        units_jsonl=units_path,
        manifest_only=False,
        symbols=("MES",),
        max_units=1,
        prior_report=first,
    )
    assert second["valid_count"] == 2
    assert second["scan_progress"]["next_offset"] == 2

    third = mod.scan_lake_data(
        repo_root=tmp_path,
        units_jsonl=units_path,
        manifest_only=False,
        symbols=("MES",),
        max_units=1,
        prior_report=second,
    )
    assert third["valid_count"] == 3
    assert third["scan_progress"]["complete"] is True
    assert third["scan_progress"]["total_source_rows"] == 3


def test_merge_prior_report_rejects_source_mismatch(tmp_path: Path) -> None:
    mod = _load_check_lake_data()
    base = mod._empty_report(source="a.jsonl", omit_valid_ids=False)
    prior = mod._empty_report(source="b.jsonl", omit_valid_ids=False)
    with pytest.raises(ValueError, match="source mismatch"):
        mod._merge_prior_report(base, prior, source="a.jsonl", omit_valid_ids=False)


def test_merge_prior_report_rejects_omit_flag_mismatch(tmp_path: Path) -> None:
    mod = _load_check_lake_data()
    base = mod._empty_report(source="units.jsonl", omit_valid_ids=True)
    prior = mod._empty_report(source="units.jsonl", omit_valid_ids=False)
    prior["valid_unit_ids"] = ["u0"]
    with pytest.raises(ValueError, match="omit_valid_ids mismatch"):
        mod._merge_prior_report(base, prior, source="units.jsonl", omit_valid_ids=True)


def test_load_report_json_invalid(tmp_path: Path) -> None:
    mod = _load_check_lake_data()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        mod.load_report_json(bad)
