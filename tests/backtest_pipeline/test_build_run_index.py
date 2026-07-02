from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_run_index.py"
_spec = importlib.util.spec_from_file_location("build_run_index", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["build_run_index"] = _mod
_spec.loader.exec_module(_mod)

build_run_index = _mod.build_run_index


def _write_run_dir(
    root: Path,
    run_id: str,
    *,
    realized: float,
    gate3: str = "",
    gate4: str = "",
    created_at_utc: str = "2026-07-02T10:00:00+00:00",
) -> None:
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run_manifest.json").write_text(
        json.dumps({"run_id": run_id, "canonical_model_id": "SECOND_WAVE_CONTINUATION",
                    "symbol": "MES", "event_id": "CPI_2024_09_11_TIGHT",
                    "created_at_utc": created_at_utc,
                    "strategy_params": {"signal_threshold": 0.05}}),
        encoding="utf-8",
    )
    (run_dir / "stats_summary.json").write_text(
        json.dumps({"run_id": run_id, "mechanical_validity_status": "pass",
                    "economic_result_status": "pass",
                    "realized_closed_trade_pnl": realized, "exit_reason": "take_profit",
                    "exit_leg_enabled": True, "fills_count": 2}),
        encoding="utf-8",
    )
    (run_dir / "data_validation.json").write_text(
        json.dumps({"data_validation_status": "pass"}), encoding="utf-8"
    )
    if gate3:
        (run_dir / "gate3_sensitivity.json").write_text(
            json.dumps({"status": gate3, "min_realized_closed_trade_pnl": realized,
                        "axes_unavailable_upstream": []}),
            encoding="utf-8",
        )
    if gate4:
        (run_dir / "robustness_report.json").write_text(
            json.dumps({"status": gate4, "psr": 0.78, "dsr": 0.47, "cscv": {"pbo": 0.5}}),
            encoding="utf-8",
        )
    (run_dir / "promotion_decision.json").write_text(
        json.dumps({"decision": "observe", "promotion_allowed": False}), encoding="utf-8"
    )


def test_index_joins_stats_gates_and_decision(tmp_path: Path) -> None:
    root = tmp_path / "hbt_runs"
    _write_run_dir(root, "run_a", realized=20.75, gate3="pass", gate4="fail")
    _write_run_dir(root, "run_b", realized=-1.25)
    out = tmp_path / "index.jsonl"

    summary = build_run_index([root], out)

    assert summary["rows"] == 2
    lines = out.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines[:-1]]
    tail = json.loads(lines[-1])
    assert tail["row_kind"] == "index_summary"
    by_id = {row["run_id"]: row for row in rows}
    assert by_id["run_a"]["realized_closed_trade_pnl"] == 20.75
    assert by_id["run_a"]["gate3_status"] == "pass"
    assert by_id["run_a"]["gate4_status"] == "fail"
    assert by_id["run_a"]["gate4_pbo"] == 0.5
    assert by_id["run_a"]["promotion_allowed"] is False
    assert by_id["run_a"]["stats_summary_sha256"]
    assert by_id["run_b"]["gate3_status"] == ""


def test_index_rebuild_is_byte_identical(tmp_path: Path) -> None:
    root = tmp_path / "hbt_runs"
    _write_run_dir(root, "run_a", realized=1.0, gate3="pass")
    _write_run_dir(root, "run_b", realized=2.0)
    out1 = tmp_path / "one.jsonl"
    out2 = tmp_path / "two.jsonl"

    build_run_index([root], out1)
    build_run_index([root], out2)

    assert (
        hashlib.sha256(out1.read_bytes()).hexdigest()
        == hashlib.sha256(out2.read_bytes()).hexdigest()
    )


def test_non_run_dirs_and_corrupt_json_are_skipped(tmp_path: Path) -> None:
    root = tmp_path / "hbt_runs"
    (root / "empty_dir").mkdir(parents=True)
    corrupt = root / "corrupt"
    corrupt.mkdir()
    (corrupt / "stats_summary.json").write_text("{not json", encoding="utf-8")
    out = tmp_path / "index.jsonl"

    summary = build_run_index([root], out)

    # empty_dir has no artifacts -> skipped; corrupt stats -> row exists with
    # honest 'missing' statuses (the file exists, its content did not parse).
    assert summary["run_dirs_scanned"] == 2
    lines = out.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines[:-1]]
    assert len(rows) == 1
    assert rows[0]["run_id"] == "corrupt"
    assert rows[0]["mechanical_validity_status"] == "missing"


def test_manifest_only_dirs_excluded_and_named(tmp_path: Path) -> None:
    # Blocked/dry-run dirs write run_manifest.json but never stats_summary.json.
    # They must not become result rows; the summary names them.
    root = tmp_path / "hbt_runs"
    _write_run_dir(root, "run_ok", realized=1.0)
    blocked = root / "run_blocked"
    blocked.mkdir()
    (blocked / "run_manifest.json").write_text(
        json.dumps({"run_id": "run_blocked", "canonical_model_id": "X"}), encoding="utf-8"
    )
    out = tmp_path / "index.jsonl"

    summary = build_run_index([root], out)

    assert summary["rows"] == 1
    assert summary["run_dirs_incomplete_no_stats"] == ["run_blocked"]
    lines = out.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines[:-1]]
    assert [r["run_id"] for r in rows] == ["run_ok"]


def test_rows_ordered_chronologically_not_lexicographically(tmp_path: Path) -> None:
    # Gate-4 groups per-event runs chronologically; run ids are not
    # guaranteed time-ordered, so the index must sort by created_at_utc.
    root = tmp_path / "hbt_runs"
    _write_run_dir(root, "run_z_early", realized=1.0,
                   created_at_utc="2026-07-01T09:00:00+00:00")
    _write_run_dir(root, "run_a_late", realized=2.0,
                   created_at_utc="2026-07-02T09:00:00+00:00")
    out = tmp_path / "index.jsonl"

    build_run_index([root], out)

    lines = out.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines[:-1]]
    assert [r["run_id"] for r in rows] == ["run_z_early", "run_a_late"]
    assert rows[0]["created_at_utc"] == "2026-07-01T09:00:00+00:00"
