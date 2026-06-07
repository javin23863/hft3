from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATION_MATRIX = ROOT / "docs" / "project" / "VALIDATION_MATRIX.md"
TRACEABILITY = ROOT / "docs" / "hft3_traceability.md"
RUNBOOK = ROOT / "docs" / "hft3_autonomous_pipeline_runbook.md"
PHASE25_TEST = "tests/test_phase25_required_tests.py"
PHASE25_COMMAND = (
    '$env:PYTHONPATH = "packages;apps"; '
    "python -m pytest tests/test_phase25_required_tests.py -q"
)
EXPECTED_SCOREBOARD = {
    "tests/test_autonomous_runner.py": 11,
    "tests/test_autonomous_runner_recovery.py": 13,
    "tests/test_phase25_required_tests.py": 5,
    "tests/test_runner_honesty.py": 6,
    "tests/test_research_intake.py": 11,
    "tests/test_extractors.py": 14,
    "tests/test_workbench/test_phase5_trade_audit.py": 10,
    "tests/test_workbench/test_robustness_pack_phase9.py": 12,
    "tests/test_defensive_model.py": 16,
    "tests/test_gate_schema.py": 14,
    "tests/test_certification_registry_hardening.py": 19,
    "tests/test_backtester_certification_governance.py": 8,
    "tests/test_promotion_record.py": 12,
    "tests/test_data_class.py": 20,
    "tests/test_workbench/test_double_wf.py": 16,
    "tests/test_artifact_bundle.py": 11,
    "tests/test_trade_manager_phase14.py": 6,
    "tests/test_trade_manager_phase15.py": 9,
    "tests/test_trade_manager_phase16.py": 10,
    "tests/test_trade_manager_phase17.py": 41,
    "tests/test_trade_manager_phase18.py": 23,
    "tests/test_trade_manager_phase19.py": 22,
    "tests/test_trade_manager_phase20.py": 11,
    "tests/test_trade_manager_phase21.py": 12,
    "tests/test_observer_view_read_only.py": 10,
    "tests/test_trade_manager_phase23.py": 10,
}
EXPECTED_PHASE_GATES = {
    "Phase 20": "python -m pytest tests/test_trade_manager_phase20.py -q",
    "Phase 21": "python -m pytest tests/test_trade_manager_phase21.py -q",
    "Phase 22": "python -m pytest tests/test_observer_view_read_only.py -q",
    "Phase 23": "python -m pytest tests/test_trade_manager_phase23.py -q",
    "Phase 24": "python -m pytest tests/test_autonomous_runner.py tests/test_autonomous_runner_recovery.py -q",
    "Phase 25": "python -m pytest tests/test_phase25_required_tests.py -q",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(markdown: str, heading: str) -> str:
    pattern = rf"(?ms)^## {re.escape(heading)}\s*$\n(?P<body>.*?)(?=^##\s|\Z)"
    match = re.search(pattern, markdown)
    assert match, f"Missing section: {heading}"
    return match.group("body")


def _phase25_matrix_row(markdown: str) -> str:
    rows = [line for line in markdown.splitlines() if line.startswith("| Phase 25")]
    assert rows, "Validation matrix must include a Phase 25 row"
    assert len(rows) == 1, "Validation matrix should have one Phase 25 row"
    return rows[0]


def _table_row(markdown: str, label: str) -> str:
    rows = [line for line in markdown.splitlines() if line.startswith(f"| {label}")]
    assert len(rows) == 1, label
    return rows[0]


def _scoreboard(markdown: str) -> tuple[int, int, list[tuple[str, int]]]:
    header = re.search(
        r"\*\*(?P<passed>\d+)/(?P<total>\d+) passing\*\* across (?P<files>\d+) test files:",
        markdown,
    )
    assert header, "Runbook scoreboard header is missing"
    assert header.group("passed") == header.group("total")

    total = int(header.group("total"))
    files = int(header.group("files"))
    entries = [
        (path, int(count))
        for path, count in re.findall(r"^- `([^`]+)` \((\d+) tests\)", markdown, re.MULTILINE)
    ]
    return total, files, entries


def test_phase25_validation_matrix_has_concrete_gate() -> None:
    matrix = _read(VALIDATION_MATRIX)
    row = _phase25_matrix_row(matrix)

    assert PHASE25_COMMAND in row
    assert "missing required tests" not in row.lower()
    for phase, command in EXPECTED_PHASE_GATES.items():
        phase_rows = [line for line in matrix.splitlines() if line.startswith(f"| {phase}")]
        assert len(phase_rows) == 1, phase
        assert command in phase_rows[0], phase


def test_phase25_traceability_is_closed() -> None:
    markdown = _read(TRACEABILITY)
    section = _section(markdown, "Phase 25: Tests")

    assert PHASE25_TEST in section
    assert "22 required tests" not in section
    assert "PARTIAL" not in section
    assert "PARTIALLY IMPLEMENTED" not in section
    assert re.search(r"\|\s*25\s+[^|]*\|\s*[^|]*DONE[^|]*\|", markdown, re.IGNORECASE)


def test_runbook_no_longer_claims_phase25_missing() -> None:
    markdown = _read(RUNBOOK)
    completed = _section(markdown, "Completed phases (26 of 26)")

    assert "~5 missing" not in markdown
    assert "Most exist" not in markdown
    assert "22 required tests" not in markdown
    assert "26 of 26" in markdown
    assert "Required tests and validation matrix" in completed


def test_scoreboard_counts_are_internally_consistent() -> None:
    runbook = _read(RUNBOOK)
    matrix = _read(VALIDATION_MATRIX)
    traceability = _read(TRACEABILITY)
    total, files, entries = _scoreboard(runbook)
    scoreboard = dict(entries)

    assert len(entries) == files
    assert sum(count for _, count in entries) == total
    assert scoreboard == EXPECTED_SCOREBOARD
    for test_file in EXPECTED_SCOREBOARD:
        assert (ROOT / test_file).is_file(), test_file

    expected = f"{total}/{total} passing"
    expected_files = f"{files} test files"
    assert expected in matrix
    assert expected_files in matrix
    assert expected in traceability
    assert expected_files in traceability


def test_required_blockers_remain_documented() -> None:
    matrix = _read(VALIDATION_MATRIX)
    runbook = _read(RUNBOOK)

    assert re.search(r"Scaffolded mode.*WorkbenchEngine", runbook)
    assert re.search(r"Single-WF only.*double-WF", runbook)
    assert "External broker/Rithmic routing remains unimplemented" in runbook
    chi404_row = _table_row(matrix, "CHI404 remote gates")
    cpp_row = _table_row(matrix, "C++ golden binaries")
    assert "not local by default" in chi404_row
    assert "explicit CHI404 validation run" in chi404_row
    assert "skip if binaries absent" in cpp_row
    assert "build required target first" in cpp_row
