"""Paid data inventory and non-destructive sync tests."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path

from scripts.paid_data_inventory import DEFAULT_CME_SYMBOLS, build_q001_cme_data_inventory, build_report, write_reports


def _manifest_row(path, event_id, symbol, event_count=1):
    payload = b"npz-bytes"
    path.write_bytes(payload)
    return {
        "event_id": event_id,
        "symbol": symbol,
        "npz_path": str(path),
        "event_count": event_count,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "created_utc": "2026-06-14T00:00:00+00:00",
    }


def _markdown_table_rows(markdown: str) -> list[list[str]]:
    rows = []
    for line in markdown.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or set(cells[0]) <= {"-", ":"}:
            continue
        rows.append(cells)
    return rows


_Q001_RE = re.compile(r"\bQ001\b", flags=re.IGNORECASE)
_Q001_FINAL_STATE_TOKEN_RE = re.compile(
    r"\b(?:"
    r"owner[\s_-]+accepted|owner[\s_-]+approval|owner[\s_-]+approved|"
    r"owner[\s_-]+acceptance[\s_-]+received|"
    r"accepted|approved|green|closed|completed?|resolved|certified|pass(?:ed)?|unblocked"
    r")\b",
    flags=re.IGNORECASE,
)


def _normalize_markdown_unit(lines: list[str]) -> str:
    return " ".join(" ".join(line.strip().split()) for line in lines).strip()


def _normalized_markdown_units(markdown: str) -> list[tuple[int, str]]:
    units = []
    pending_lines = []
    pending_start_line = 0

    for line_no, line in enumerate(markdown.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            if pending_lines:
                units.append((pending_start_line, _normalize_markdown_unit(pending_lines)))
                pending_lines = []
                pending_start_line = 0
            continue

        if stripped.startswith("|"):
            if pending_lines:
                units.append((pending_start_line, _normalize_markdown_unit(pending_lines)))
                pending_lines = []
                pending_start_line = 0
            units.append((line_no, _normalize_markdown_unit([stripped])))
            continue

        if not pending_lines:
            pending_start_line = line_no
        pending_lines.append(stripped)

    if pending_lines:
        units.append((pending_start_line, _normalize_markdown_unit(pending_lines)))

    return units


def _q001_final_state_units(doc_name: str, markdown: str) -> list[tuple[str, int, str]]:
    claims = []
    for line_no, unit in _normalized_markdown_units(markdown):
        if _Q001_RE.search(unit) and _Q001_FINAL_STATE_TOKEN_RE.search(unit):
            claims.append((doc_name, line_no, unit))
    return claims


def _unexpected_q001_final_state_units(docs: dict[str, str], allowed_units: set[tuple[str, str]]) -> list[str]:
    claims = []
    for doc_name, markdown in docs.items():
        for claim_doc_name, line_no, unit in _q001_final_state_units(doc_name, markdown):
            if (claim_doc_name, unit) not in allowed_units:
                claims.append(f"{claim_doc_name}:{line_no}: {unit}")
    return claims


def _section(markdown: str, heading: str, next_heading: str) -> str:
    return markdown.split(heading, 1)[1].split(next_heading, 1)[0]


def _event_type_from_event_id(event_id: str) -> str:
    parts = event_id.split("_")
    for idx, part in enumerate(parts):
        if len(part) == 4 and part.isdigit():
            return "_".join(parts[:idx])
    return parts[0]


def _date_from_event_id(event_id: str) -> str:
    parts = event_id.split("_")
    for idx in range(len(parts) - 2):
        year, month, day = parts[idx : idx + 3]
        if (
            len(year) == 4
            and year.isdigit()
            and len(month) == 2
            and month.isdigit()
            and len(day) == 2
            and day.isdigit()
        ):
            value = f"{year}-{month}-{day}"
            date.fromisoformat(value)
            return value
    raise AssertionError(f"event id has no YYYY_MM_DD date: {event_id}")


def _unquote_markdown_code(value: str) -> str:
    return value.replace("`", "")


def test_paid_data_inventory_sync_reports_runnable_and_raw_separately(tmp_path):
    repo = tmp_path / "repo"
    source = tmp_path / "paid" / "data"
    npz = source / "npz" / "MES.v.0_CPI_2025_02_12_TIGHT_mbo.npz"
    raw = source / "replay" / "mbp10" / "MES.v.0_CPI_2025_03_12_TIGHT_mbp-10.dbn.zst"
    root_raw = source / "NFP_2025_04_04_TIGHT_mbo.dbn.zst"
    equity = source / "equities" / "daily" / "ABCD.parquet"
    crypto = source / "crypto" / "gold" / "BTCUSDT_2024-01-01.parquet"
    option = source / "options" / "equity_chains" / "raw" / "gme_2021" / "gme_2021_cbbo-1m.dbn.zst"
    for path in (npz, raw, root_raw, equity, crypto, option):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"paid-data")

    report = build_report(repo_root=repo, source_root=source, sync=True, dry_run=False)

    assert (repo / "data" / "npz" / npz.name).is_file()
    assert (repo / "data" / "replay" / "mbp10" / raw.name).is_file()
    assert (repo / "data" / "raw" / "databento_mbo" / root_raw.name).is_file()
    assert (repo / "data" / "equities" / "daily" / equity.name).is_file()
    assert (repo / "data" / "crypto" / "gold" / crypto.name).is_file()
    assert report["official_coverage_status"] == "RUNNABLE_CME_NPZ_PRESENT"
    assert report["runnable_npz_days"]["MES"] == 1
    assert report["raw_download_days"]["MES"] == 1
    assert report["raw_download_days"]["unspecified"] == 1
    assert report["missing_conversion_days"]["MES"] == 1


def test_paid_data_inventory_does_not_overwrite_conflicts(tmp_path):
    repo = tmp_path / "repo"
    source = tmp_path / "paid" / "data"
    source_npz = source / "npz" / "MES.v.0_CPI_2025_02_12_TIGHT_mbo.npz"
    dest_npz = repo / "data" / "npz" / source_npz.name
    source_npz.parent.mkdir(parents=True, exist_ok=True)
    dest_npz.parent.mkdir(parents=True, exist_ok=True)
    source_npz.write_bytes(b"source-paid-data")
    dest_npz.write_bytes(b"existing")

    report = build_report(repo_root=repo, source_root=source, sync=True, dry_run=False)

    assert dest_npz.read_bytes() == b"existing"
    assert any(row["action"] == "conflict_not_overwritten" for row in report["synced_files"])


def test_paid_data_inventory_dry_run_reports_planned_copy_not_copied(tmp_path):
    repo = tmp_path / "repo"
    source = tmp_path / "paid" / "data"
    source_npz = source / "npz" / "MES.v.0_CPI_2025_02_12_TIGHT_mbo.npz"
    source_npz.parent.mkdir(parents=True, exist_ok=True)
    source_npz.write_bytes(b"source-paid-data")

    report = build_report(repo_root=repo, source_root=source, sync=True, dry_run=True)

    assert not (repo / "data" / "npz" / source_npz.name).exists()
    assert any(row["action"] == "planned_copy" for row in report["synced_files"])
    assert not any(row["action"] == "copied" for row in report["synced_files"])


def test_paid_data_inventory_writes_runtime_reports(tmp_path):
    report = {
        "generated_at_utc": "2026-06-05T00:00:00+00:00",
        "data_root_used": str(tmp_path / "repo" / "data"),
        "source_root": str(tmp_path / "paid" / "data"),
        "sync_requested": False,
        "dry_run": True,
        "official_coverage_status": "NO_RUNNABLE_CME_NPZ",
        "runnable_npz_days": {},
        "raw_download_days": {},
        "missing_conversion_days": {},
        "source_inventory": {"categories": {}},
        "synced_files": [],
    }
    json_path, md_path = write_reports(report, tmp_path / "runtime" / "data_audits")

    assert json.loads(json_path.read_text(encoding="utf-8"))["official_coverage_status"] == "NO_RUNNABLE_CME_NPZ"
    assert "Raw DBN/MBP10 files are downloaded backlog" in md_path.read_text(encoding="utf-8")


def test_paid_data_inventory_markdown_splits_options_strict_and_study_coverage(tmp_path):
    report = {
        "generated_at_utc": "2026-06-15T00:00:00+00:00",
        "data_root_used": str(tmp_path / "repo" / "data"),
        "source_root": str(tmp_path / "paid" / "data"),
        "sync_requested": False,
        "dry_run": True,
        "official_coverage_status": "RUNNABLE_CME_NPZ_PRESENT",
        "runnable_npz_days": {},
        "raw_download_days": {},
        "missing_conversion_days": {},
        "source_inventory": {"categories": {}},
        "synced_files": [],
        "q001_cme_data_inventory": {
            "status": "INVENTORIED_WITH_WARNINGS",
            "scope": "read-only local inventory",
            "event_catalog": {"status": "OK", "symbols": ["MES.v.0"]},
            "futures": {
                "active_npz_manifest": {
                    "status": "OK",
                    "record_count": 1,
                    "date_min": "2024-01-01",
                    "date_max": "2024-01-01",
                    "sha256_validation_mode": "content_verified",
                },
                "mbo_pilot_basket": {
                    "status": "completed_with_gaps",
                    "present_runnable_npz_slots": 3,
                    "expected_event_symbol_slots": 4,
                    "coverage_pct": 75.0,
                    "missing_or_unavailable_slots": 1,
                },
            },
            "options": {
                "data_doctor_status": "WARN",
                "options_lane": {
                    "fixing_mbo": {
                        "dates_covered": 275,
                        "study_dates_covered": 782,
                        "study_date_list": ["2023-09-15"],
                        "trade_only_dates": 507,
                        "first_date": "2023-05-01",
                        "last_date": "2024-06-03",
                    },
                    "expiry_coverage": {
                        "expected_dates": 784,
                        "dates_covered": 784,
                        "covered_elsewhere": ["2023-09-15", "2024-09-18", "2025-06-20"],
                        "gap_count": 0,
                        "strict_mbo_gap_count": 507,
                        "strict_mbo_stale_gap_count": 503,
                    },
                },
            },
            "gaps": [],
        },
    }
    _, md_path = write_reports(report, tmp_path / "runtime" / "data_audits")
    markdown = md_path.read_text(encoding="utf-8")

    assert "strict_quote_dates=275" in markdown
    assert "study_file_dates=782" in markdown
    assert "coverage_dates=784/784" in markdown
    assert "trade_only_dates=507" in markdown
    assert "covered_elsewhere=3 (net_new=2; overlap=1)" in markdown
    assert "study_gap_count=0" in markdown
    assert "strict_quote_gap_count=507" in markdown
    assert "strict_quote_stale=503" in markdown
    assert "| CME options fixing MBO | WARN | 275 dates;" not in markdown


def test_q001_project_docs_keep_owner_decision_gate_fail_closed():
    repo_root = Path(__file__).resolve().parents[2]
    status_doc = (repo_root / "docs" / "project" / "Q001_DATA_INVENTORY_STATUS.md").read_text(encoding="utf-8")
    owner_packet = (repo_root / "docs" / "project" / "Q001_OWNER_DECISION_PACKET.md").read_text(encoding="utf-8")
    open_questions = (repo_root / "docs" / "project" / "OPEN_QUESTIONS_AND_REJECTIONS.md").read_text(
        encoding="utf-8"
    )
    mbo_gap_ledger = (repo_root / "docs" / "project" / "Q001_MBO_GAP_REJECTION_LEDGER.md").read_text(
        encoding="utf-8"
    )
    options_warning_ledger = (
        repo_root / "docs" / "project" / "Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md"
    ).read_text(encoding="utf-8")
    acceptance_checklist = (repo_root / "docs" / "project" / "ACCEPTANCE_CHECKLIST.md").read_text(encoding="utf-8")
    q001_docs = {
        "Q001_DATA_INVENTORY_STATUS.md": status_doc,
        "Q001_OWNER_DECISION_PACKET.md": owner_packet,
        "OPEN_QUESTIONS_AND_REJECTIONS.md": open_questions,
        "Q001_MBO_GAP_REJECTION_LEDGER.md": mbo_gap_ledger,
        "Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md": options_warning_ledger,
        "ACCEPTANCE_CHECKLIST.md": acceptance_checklist,
    }
    allowed_q001_final_state_units = {
        (
            "Q001_DATA_INVENTORY_STATUS.md",
            "- Q001 scope is read-only local inventory and available-data research gating, not model execution or promotion evidence. - Hash verification was enabled: `verify_q001_hashes=true`. - Active NPZ manifest status is `OK`: `record_count=60643`, `date_min=2018-01-01`, `date_max=2026-06-04`, `missing_npz_files=0`, `invalid_sha256_rows=0`, `sha256_content_verified=true`, `sha256_validation_mode=content_verified`. - MBO pilot status is `completed_with_gaps`: `present_runnable_npz_slots=4829`, `expected_event_symbol_slots=5040`, `missing_or_unavailable_slots=211`, `coverage_pct=95.8135`. - MBO pilot missing-slot taxonomy is verified: `203` slots are full `no_market_data` windows (`29` windows * `7` symbols) and `8` slots are partial FED_H41 symbol absences after redownload. - The event/window rejection ledger is owner-accepted for inventory scope; affected model cells must remain explicit skips or rejections until data is filled. - Options `data_doctor` status is `WARN` only due to `options-fixing-mbo-coverage`; study coverage has `gap_count=0`, `expiry_coverage.dates_covered=784/784`, and `fail_checks=[]`. - The strict options MBO warning ledger is owner-accepted for available-data inventory scope; strict quote-only options models remain sidelined until strict quote coverage is filled or separately scoped out.",
        ),
        (
            "Q001_OWNER_DECISION_PACKET.md",
            "Q001 has an explicit owner decision. An agent must not widen the accepted scope from this record.",
        ),
        (
            "Q001_OWNER_DECISION_PACKET.md",
            "- Acceptance is limited to Q001 available-data inventory scope. - Acceptance does not prove model readiness, PIT joins, robustness, promotion eligibility, or options lane readiness. - The cockpit may mark the Q001 available-data inventory gate OK only when the accepted decision artifact is present and no unaccepted Q001 warning/failure remains; this is not model readiness. - Full-universe research must treat rejected or unavailable MBO slots as explicit skip/rejection reasons unless the data is filled later. - Strict options quote reconstruction, strict quote-only MBO features, options order-book replay, and options model promotion remain blocked until strict quote coverage is filled or separately scoped out.",
        ),
        (
            "Q001_OWNER_DECISION_PACKET.md",
            "```text Owner decision date: 2026-06-15 MBO pilot gap decision: ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE Options strict MBO warning decision: ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE Accepted inventory scope: available-data research may proceed with explicit coverage/skip/rejection reasons Rejected model scope, if any: missing-MBO-required and strict-options-quote-required models are sidelined until filled or separately scoped out Required future data fill, if any: futures MBO missing slots and options strict quote MBO gaps are non-blocking side-lane backlog Post-decision verifier command: python scripts\\paid_data_inventory.py --dry-run --verify-q001-hashes Post-decision verifier result: raw report remains INVENTORIED_WITH_WARNINGS; owner decision is ACCEPTED_AVAILABLE_DATA_SCOPE Q001 final status after rerun: ACCEPTED_AVAILABLE_DATA_SCOPE for available-data inventory gate only ```",
        ),
        (
            "OPEN_QUESTIONS_AND_REJECTIONS.md",
            "| Q001 | What exact CME futures/options historical datasets are available for full universe research after the lane split? | F001, F002, F005 | Full model-universe claims require real coverage, not assumed coverage. | Owner decision recorded: `ACCEPTED_AVAILABLE_DATA_SCOPE`. See [Q001_DATA_INVENTORY_STATUS.md](Q001_DATA_INVENTORY_STATUS.md) and [Q001_OWNER_DECISION_PACKET.md](Q001_OWNER_DECISION_PACKET.md). The 2026-06-14 `python scripts\\paid_data_inventory.py --dry-run --verify-q001-hashes` run content-verified the active NPZ manifest and classified the MBO gaps as `203` full no-market slots plus `8` partial symbol absences. [Q001_MBO_GAP_REJECTION_LEDGER.md](Q001_MBO_GAP_REJECTION_LEDGER.md) and [Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md](Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md) are accepted as non-blocking for available-data inventory scope only. Missing-MBO-required models and strict-options-quote-required models are sidelined until data is filled or separately scoped out; available-data models may run with explicit coverage, skip, or rejection reasons. |",
        ),
        (
            "Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md",
            "The project owner accepts this ledger for Q001 available-data inventory scope. The accepted meaning is limited to:",
        ),
    }
    long_bad_q001_claim = "Q001 " + " ".join(["still blocked"] * 80) + " passed."
    assert _unexpected_q001_final_state_units(
        {
            "NEGATIVE.md": (
                "Q001 is green and accepted.\n\n"
                "Q001 is accepted.\n\n"
                f"{long_bad_q001_claim}\n\n"
                "Q001 is unblocked.\n\n"
                "Q001 status PASS."
            )
        },
        set(),
    ) == [
        "NEGATIVE.md:1: Q001 is green and accepted.",
        "NEGATIVE.md:3: Q001 is accepted.",
        f"NEGATIVE.md:5: {long_bad_q001_claim}",
        "NEGATIVE.md:7: Q001 is unblocked.",
        "NEGATIVE.md:9: Q001 status PASS.",
    ]
    forbidden_q001_final_state_units = _unexpected_q001_final_state_units(
        q001_docs,
        allowed_q001_final_state_units,
    )
    milestone_gate_rule = (
        "[ ] Current milestone/open-question gate has no required owner decision, "
        "unaccepted warning, or blocker remaining open. Accepted available-data scope must still "
        "sideline missing-data-dependent models with explicit skip/rejection reasons."
    )
    completion_gate = acceptance_checklist.split("## G. Completion Gate", 1)[1].split(
        "## H. Immediate Rejection Conditions", 1
    )[0]

    assert "Status: `ACCEPTED_AVAILABLE_DATA_SCOPE` (available-data research allowed; model-readiness not proven)" in status_doc
    assert "Source report status: `INVENTORIED_WITH_WARNINGS`" in status_doc
    assert "(`ACCEPTED_AVAILABLE_DATA_SCOPE`)" in status_doc
    assert "Strategies that require missing MBO slots or strict options quote reconstruction stay sidelined" in status_doc
    assert "Status: `ACCEPTED_AVAILABLE_DATA_SCOPE` (available-data research allowed; model-readiness not proven)" in owner_packet
    assert "Available-data models may run with explicit coverage" in owner_packet
    assert "require the missing data must stay sidelined" in owner_packet
    assert "An agent must not widen the accepted scope" in owner_packet
    assert "from this record." in owner_packet
    assert "Any new unaccepted" in owner_packet
    assert "warning or failure keeps the Q001 available-data gate not OK" in owner_packet
    assert forbidden_q001_final_state_units == []
    q001_rows = [row for row in _markdown_table_rows(open_questions) if row and row[0] == "Q001"]
    assert len(q001_rows) == 1
    q001_open_question_row = " | ".join(q001_rows[0])
    assert "`ACCEPTED_AVAILABLE_DATA_SCOPE`" in q001_open_question_row
    assert "Q001_OWNER_DECISION_PACKET.md" in q001_open_question_row
    assert "available-data models may run with explicit coverage, skip, or rejection reasons" in q001_open_question_row
    assert milestone_gate_rule in completion_gate
    decision_template_section = owner_packet.split("## Decision Record Template", 1)[1]
    decision_template_block = re.search(r"\A\s*```text\n(?P<body>.*?)\n```", decision_template_section, flags=re.DOTALL)
    assert decision_template_block is not None
    assert decision_template_block.group("body").splitlines() == [
        "Owner decision date: 2026-06-15",
        "MBO pilot gap decision: ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE",
        "Options strict MBO warning decision: ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE",
        "Accepted inventory scope: available-data research may proceed with explicit coverage/skip/rejection reasons",
        "Rejected model scope, if any: missing-MBO-required and strict-options-quote-required models are sidelined until filled or separately scoped out",
        "Required future data fill, if any: futures MBO missing slots and options strict quote MBO gaps are non-blocking side-lane backlog",
        "Post-decision verifier command: python scripts\\paid_data_inventory.py --dry-run --verify-q001-hashes",
        "Post-decision verifier result: raw report remains INVENTORIED_WITH_WARNINGS; owner decision is ACCEPTED_AVAILABLE_DATA_SCOPE",
        "Q001 final status after rerun: ACCEPTED_AVAILABLE_DATA_SCOPE for available-data inventory gate only",
    ]

    evidence_snapshot_path = repo_root / "tests" / "fixtures" / "q001_owner_packet_evidence_snapshot.json"
    evidence_snapshot = json.loads(evidence_snapshot_path.read_text(encoding="utf-8"))
    q001 = evidence_snapshot["q001"]
    active = q001["futures"]["active_npz_manifest"]
    pilot = q001["futures"]["mbo_pilot_basket"]
    options = q001["options"]
    expiry = options["options_lane"]["expiry_coverage"]
    full_no_market_slots = pilot["no_market_window_count"] * len(q001["expected_canonical_cme_symbols"])
    partial_symbol_absences = sum(len(row.get("missing_symbols") or []) for row in pilot["partial_windows"])

    assert q001["status"] == "INVENTORIED_WITH_WARNINGS"
    assert pilot["missing_or_unavailable_slots"] == full_no_market_slots + partial_symbol_absences
    assert pilot["missing_or_unavailable_slots"] > 0
    assert options["data_doctor_status"] == "WARN"
    assert expiry["strict_mbo_gap_count"] > 0
    assert expiry["strict_mbo_stale_gap_count"] > 0
    assert f"| Q001 status | `{q001['status']}` |" in owner_packet
    assert (
        f"| Hash verification | `verify_q001_hashes={str(evidence_snapshot['verify_q001_hashes']).lower()}` |"
        in owner_packet
    )
    assert f"| Active NPZ manifest rows | `{active['record_count']}` |" in owner_packet
    assert f"| Missing NPZ files | `{active['missing_npz_files']}` |" in owner_packet
    assert f"| Invalid SHA256 rows | `{active['invalid_sha256_rows']}` |" in owner_packet
    assert f"| MBO pilot missing/unavailable slots | `{pilot['missing_or_unavailable_slots']}` |" in owner_packet
    assert f"| Full no-market slots | `{full_no_market_slots}` |" in owner_packet
    assert f"| Partial FED_H41 symbol absences | `{partial_symbol_absences}` |" in owner_packet
    assert f"| Options data-doctor status | `{options['data_doctor_status']}` |" in owner_packet
    assert (
        f"| Options study coverage | `{expiry['dates_covered']}/{expiry['expected_dates']}` dates, "
        f"`gap_count={expiry['gap_count']}` |"
    ) in owner_packet
    assert (
        f"| Options strict quote MBO gaps | `{expiry['strict_mbo_gap_count']}` gaps, "
        f"`{expiry['strict_mbo_stale_gap_count']}` stale |"
    ) in owner_packet


def test_q001_options_strict_mbo_warning_ledger_matches_inventory_report():
    repo_root = Path(__file__).resolve().parents[2]
    ledger = (repo_root / "docs" / "project" / "Q001_OPTIONS_STRICT_MBO_WARNING_LEDGER.md").read_text(
        encoding="utf-8"
    )
    report = json.loads((repo_root / "runtime" / "data_audits" / "paid_data_inventory.json").read_text(encoding="utf-8"))
    q001 = report["q001_cme_data_inventory"]
    options = q001["options"]
    fixing_mbo = options["options_lane"]["fixing_mbo"]
    expiry = options["options_lane"]["expiry_coverage"]
    warn_checks = options["warn_checks"]
    fail_checks = options["fail_checks"]
    covered_elsewhere = expiry["covered_elsewhere"]
    study_dates = set(fixing_mbo["study_date_list"])
    covered_elsewhere_net_new = [value for value in covered_elsewhere if value not in study_dates]
    covered_elsewhere_overlap = [value for value in covered_elsewhere if value in study_dates]
    first_strict_gaps = expiry["strict_mbo_gap_dates"][:10]

    expected_status_line = "Status: `ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE` (not options model-readiness evidence)"
    assert re.findall(r"^Status: .+$", ledger, flags=re.MULTILINE) == [expected_status_line]
    assert ledger.count(expected_status_line) == 1
    ledger_lower = ledger.lower()
    assert "status: `accepted`" not in ledger_lower
    assert "status: `owner_accepted`" not in ledger_lower
    forbidden_q001_claim = re.compile(r"\b(?:closed?|green|certified|pass(?:ed)?|unblocked)\b", re.IGNORECASE)
    allowed_q001_claims: set[str] = set()
    paragraphs = {" ".join(raw.split()).lower() for raw in ledger.split("\n\n") if raw.strip()}
    assert allowed_q001_claims <= paragraphs
    for paragraph in paragraphs:
        if not forbidden_q001_claim.search(paragraph):
            continue
        assert paragraph in allowed_q001_claims, paragraph
    assert "The project owner accepts this ledger for Q001 available-data inventory scope." in ledger
    assert "This acceptance does not promote, certify, or validate any options model." in ledger

    assert q001["status"] == "INVENTORIED_WITH_WARNINGS"
    assert q001["status"] not in {"ACCEPTED", "CLOSED", "GREEN", "OK", "PASS", "COMPLETED", "RESOLVED", "CERTIFIED"}
    assert options["data_doctor_status"] == "WARN"
    assert [row["name"] for row in warn_checks] == ["options-fixing-mbo-coverage"]
    assert warn_checks[0]["status"] == "WARN"
    assert fail_checks == []
    assert expiry["coverage_mode"] == "fixing_study_trade_or_mbo"
    assert len(covered_elsewhere) == 3
    assert len(covered_elsewhere_net_new) == 2
    assert len(covered_elsewhere_overlap) == 1
    assert expiry["dates_covered"] == expiry["expected_dates"]
    assert expiry["gap_count"] == 0
    assert expiry["stale_gap_count"] == 0
    assert fixing_mbo["study_dates_covered"] + len(covered_elsewhere_net_new) == expiry["dates_covered"]
    assert fixing_mbo["quote_files"] == fixing_mbo["dates_covered"]
    assert fixing_mbo["trades_files"] == fixing_mbo["trade_only_dates"]
    assert fixing_mbo["invalid_files"] == 0
    for value in covered_elsewhere + first_strict_gaps + [fixing_mbo["first_date"], fixing_mbo["last_date"]]:
        date.fromisoformat(value)

    current_evidence_table = _markdown_table_rows(
        _section(ledger, "## Current Evidence", "First strict quote gaps recorded by the report:")
    )
    assert current_evidence_table[0] == ["Field", "Value", "Meaning"]
    assert current_evidence_table[1:] == [
        [
            "Q001 options status",
            f"`{options['data_doctor_status']}`",
            "One warning remains in the options data-doctor surface.",
        ],
        ["Warn check", f"`{warn_checks[0]['name']}`", "Strict quote-only MBO diagnostic."],
        ["Fail checks", f"`{len(fail_checks)}`", "No options fail checks are present in the Q001 report."],
        [
            "Study coverage mode",
            f"`{expiry['coverage_mode']}`",
            "Study coverage may use quote files, trade files, or validated active NPZ manifest coverage.",
        ],
        ["Expected expiry dates", f"`{expiry['expected_dates']}`", "Rule-based options expiry calendar expectation."],
        [
            "Expiry coverage dates covered",
            f"`{expiry['dates_covered']}/{expiry['expected_dates']}`",
            "Union coverage reaches Q001 inventory scope after allowed alternate active NPZ coverage is counted.",
        ],
        [
            "Raw fixing MBO study file dates",
            f"`{fixing_mbo['study_dates_covered']}`",
            "Dates covered directly by fixing MBO quote/trade files before active NPZ manifest coverage is counted.",
        ],
        [
            "Covered elsewhere",
            f"`{len(covered_elsewhere)}`",
            "Expected dates covered by validated active NPZ manifest evidence.",
        ],
        [
            "Covered-elsewhere net-new dates",
            f"`{len(covered_elsewhere_net_new)}`",
            "Dates added to the study coverage union: `2024-09-18`, `2025-06-20`.",
        ],
        [
            "Covered-elsewhere overlap",
            f"`{len(covered_elsewhere_overlap)}`",
            "Date already present in raw fixing MBO study coverage: `2023-09-15`.",
        ],
        ["Study gap count", f"`{expiry['gap_count']}`", f"No missing dates under `{expiry['coverage_mode']}`."],
        ["Study stale gap count", f"`{expiry['stale_gap_count']}`", "No stale study-coverage gaps."],
        [
            "Strict quote files",
            f"`{fixing_mbo['quote_files']}`",
            "Dates covered by strict quote-level fixing MBO files.",
        ],
        [
            "Strict quote gap count",
            f"`{expiry['strict_mbo_gap_count']}`",
            "Expected dates not covered by strict quote-level fixing MBO quotes or alternate active NPZ coverage.",
        ],
        [
            "Strict quote stale gap count",
            f"`{expiry['strict_mbo_stale_gap_count']}`",
            "Strict quote gaps older than the vendor-lag grace window.",
        ],
        ["Trade files", f"`{fixing_mbo['trades_files']}`", "Dates with trade-only fixing MBO files."],
        [
            "Trade-only dates",
            f"`{fixing_mbo['trade_only_dates']}`",
            "Trade coverage that satisfies study coverage but not strict quote reconstruction.",
        ],
        ["Invalid fixing files", f"`{fixing_mbo['invalid_files']}`", "No invalid fixing files in the report."],
    ]

    gap_block = _section(ledger, "First strict quote gaps recorded by the report:", "## Code Boundary")
    recorded_gaps = [
        line.strip()
        for line in gap_block.splitlines()
        if line.strip() and not line.strip().startswith("```")
    ]
    assert recorded_gaps == first_strict_gaps

    code_boundary_table = _markdown_table_rows(_section(ledger, "## Code Boundary", "## Acceptance Boundary"))
    assert code_boundary_table == [
        ["Check", "Mode", "Gate behavior"],
        ["`options-fixing-coverage`", "`fixing_study_trade_or_mbo`", "Fails if study coverage has gaps."],
        [
            "`options-fixing-mbo-coverage`",
            "`strict_mbo_quotes`",
            "`warn_only=True`; warns if strict quote-only MBO has gaps.",
        ],
    ]
    code_boundary = _section(ledger, "## Code Boundary", "## Acceptance Boundary")
    for check_name in [
        "options-fixing-coverage",
        "options-fixing-mbo",
        "options-ohlcv",
        "options-definitions",
        "options-statistics",
    ]:
        assert f"`{check_name}`" in code_boundary


def test_q001_mbo_gap_rejection_ledger_arithmetic_matches_manifest():
    repo_root = Path(__file__).resolve().parents[2]
    ledger = (repo_root / "docs" / "project" / "Q001_MBO_GAP_REJECTION_LEDGER.md").read_text(encoding="utf-8")
    manifest = json.loads(
        (repo_root / "packages" / "data_system" / "config" / "mbo_pilot_basket_20260605_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    no_market_windows = manifest["no_market_windows"]
    partial_windows = manifest["partial_windows"]
    partial_slot_count = sum(len(window["missing_symbols"]) for window in partial_windows)
    missing_by_event_type = manifest["missing_npz_slots_by_event_type"]
    canonical_symbols = re.findall(
        r"`([^`]+)`",
        _section(ledger, "Canonical pilot symbols:", "## Event-Type Summary"),
    )
    assert canonical_symbols == list(DEFAULT_CME_SYMBOLS)
    symbol_count = len(canonical_symbols)
    no_market_slot_count = len(no_market_windows) * symbol_count

    assert ledger.count("Status: `ACCEPTED_NON_BLOCKING_INVENTORY_SCOPE` (not model-readiness evidence)") == 1
    ledger_lower = ledger.lower()
    assert "status: `accepted`" not in ledger_lower
    assert "status: `owner_accepted`" not in ledger_lower
    forbidden_q001_claim = re.compile(r"\b(?:closed?|green|certified|pass(?:ed)?|unblocked)\b", re.IGNORECASE)
    allowed_q001_claims: set[str] = set()
    paragraphs = {" ".join(raw.split()).lower() for raw in ledger.split("\n\n") if raw.strip()}
    assert allowed_q001_claims <= paragraphs
    for paragraph in paragraphs:
        if not forbidden_q001_claim.search(paragraph):
            continue
        assert paragraph in allowed_q001_claims, paragraph
    assert "The project owner accepts these rows for available-data inventory scope only." in ledger
    assert "This does not certify model readiness, robustness, PIT joins, or promotion eligibility." in ledger
    assert (
        manifest["coverage"]["expected_event_symbol_slots"] - manifest["coverage"]["present_runnable_npz_slots"]
        == manifest["coverage"]["missing_or_unavailable_slots"]
    )
    assert no_market_slot_count + partial_slot_count == manifest["coverage"]["missing_or_unavailable_slots"]

    slot_table = _markdown_table_rows(_section(ledger, "## Slot Arithmetic", "## Event-Type Summary"))
    assert slot_table[0] == ["Class", "Window count", "Slot count", "Treatment"]
    slot_rows = slot_table[1:]
    assert len(slot_rows) == 3
    assert all(len(row) == 4 for row in slot_rows)
    assert [row[0] for row in slot_rows] == [
        "Full no-market windows",
        "Partial symbol absences",
        "Total missing or unavailable",
    ]
    assert slot_rows == [
        [
            "Full no-market windows",
            str(len(no_market_windows)),
            str(no_market_slot_count),
            "Reject/skip all 7 canonical pilot symbols for each window.",
        ],
        [
            "Partial symbol absences",
            str(len(partial_windows)),
            str(partial_slot_count),
            "Reject/skip only the listed symbols for the event window.",
        ],
        [
            "Total missing or unavailable",
            str(len(no_market_windows) + len(partial_windows)),
            str(manifest["coverage"]["missing_or_unavailable_slots"]),
            "Must not be counted as runnable coverage.",
        ],
    ]
    slot_totals = {row[0]: (int(row[1]), int(row[2])) for row in slot_rows}
    assert slot_totals["Full no-market windows"] == (len(no_market_windows), no_market_slot_count)
    assert slot_totals["Partial symbol absences"] == (len(partial_windows), partial_slot_count)
    assert slot_totals["Total missing or unavailable"] == (
        len(no_market_windows) + len(partial_windows),
        manifest["coverage"]["missing_or_unavailable_slots"],
    )

    event_table = _markdown_table_rows(_section(ledger, "## Event-Type Summary", "## Full No-Market Window Rejections"))
    assert event_table[0] == ["Event type", "No-market windows", "No-market slots", "Partial slots", "Total rejected slots"]
    event_rows = event_table[1:]
    assert len(event_rows) == len(missing_by_event_type) + 1
    assert all(len(row) == 5 for row in event_rows)
    event_totals = {
        row[0]: {
            "no_market_windows": int(row[1]),
            "no_market_slots": int(row[2]),
            "partial_slots": int(row[3]),
            "total_slots": int(row[4]),
        }
        for row in event_rows
    }
    assert set(event_totals) == set(missing_by_event_type) | {"Total"}
    non_total_event_totals = {event_type: row for event_type, row in event_totals.items() if event_type != "Total"}
    for row in non_total_event_totals.values():
        assert row["total_slots"] == row["no_market_slots"] + row["partial_slots"]
    summed_event_totals = {
        "no_market_windows": sum(row["no_market_windows"] for row in non_total_event_totals.values()),
        "no_market_slots": sum(row["no_market_slots"] for row in non_total_event_totals.values()),
        "partial_slots": sum(row["partial_slots"] for row in non_total_event_totals.values()),
        "total_slots": sum(row["total_slots"] for row in non_total_event_totals.values()),
    }
    assert summed_event_totals == event_totals["Total"]
    assert summed_event_totals["total_slots"] == manifest["coverage"]["missing_or_unavailable_slots"]
    no_market_counts_by_event_type: dict[str, int] = {}
    for event_id in no_market_windows:
        event_type = _event_type_from_event_id(event_id)
        no_market_counts_by_event_type[event_type] = no_market_counts_by_event_type.get(event_type, 0) + 1
    partial_slots_by_event_type: dict[str, int] = {}
    for window in partial_windows:
        event_type = _event_type_from_event_id(window["event_id"])
        assert window["event_type"] == event_type
        partial_slots_by_event_type[event_type] = partial_slots_by_event_type.get(event_type, 0) + len(
            window["missing_symbols"]
        )
    for event_type, total_slots in missing_by_event_type.items():
        expected_no_market_windows = no_market_counts_by_event_type.get(event_type, 0)
        expected_no_market_slots = expected_no_market_windows * symbol_count
        expected_partial_slots = partial_slots_by_event_type.get(event_type, 0)
        assert event_totals[event_type] == {
            "no_market_windows": expected_no_market_windows,
            "no_market_slots": expected_no_market_slots,
            "partial_slots": expected_partial_slots,
            "total_slots": total_slots,
        }
    assert event_totals["Total"] == {
        "no_market_windows": len(no_market_windows),
        "no_market_slots": no_market_slot_count,
        "partial_slots": partial_slot_count,
        "total_slots": manifest["coverage"]["missing_or_unavailable_slots"],
    }

    no_market_table = _markdown_table_rows(
        _section(ledger, "## Full No-Market Window Rejections", "## Partial Window Symbol Rejections")
    )
    assert no_market_table[0] == ["Event type", "Event ID", "Release date", "Reason", "Rejected slots", "Rejected symbols"]
    no_market_rows = no_market_table[1:]
    assert len(no_market_rows) == len(no_market_windows)
    assert all(len(row) == 6 for row in no_market_rows)
    expected_no_market_rows = [
        [
            _event_type_from_event_id(event_id),
            f"`{event_id}`",
            _date_from_event_id(event_id),
            "`no_market_data`",
            str(symbol_count),
            "all 7 canonical pilot symbols unavailable",
        ]
        for event_id in no_market_windows
    ]
    assert no_market_rows == expected_no_market_rows
    no_market_ids = {_unquote_markdown_code(row[1]) for row in no_market_rows}
    assert len(no_market_ids) == len(no_market_rows)
    assert no_market_ids == set(no_market_windows)
    assert all(_unquote_markdown_code(row[3]) == "no_market_data" for row in no_market_rows)
    assert all(int(row[4]) == symbol_count for row in no_market_rows)

    partial_table = _markdown_table_rows(_section(ledger, "## Partial Window Symbol Rejections", "## Acceptance Decision Needed"))
    assert partial_table[0] == ["Event type", "Event ID", "Release date", "Reason", "Rejected slots", "Rejected symbols"]
    partial_rows = partial_table[1:]
    assert len(partial_rows) == len(partial_windows)
    assert all(len(row) == 6 for row in partial_rows)
    expected_partial_rows = [
        [
            _event_type_from_event_id(window["event_id"]),
            f"`{window['event_id']}`",
            window["release_date"],
            f"`{window['reason']}`",
            str(len(window["missing_symbols"])),
            ", ".join(f"`{symbol}`" for symbol in window["missing_symbols"]),
        ]
        for window in partial_windows
    ]
    for window in partial_windows:
        date.fromisoformat(window["release_date"])
        assert window["release_date"] == _date_from_event_id(window["event_id"])
    assert partial_rows == expected_partial_rows
    partial_by_event_id = {_unquote_markdown_code(row[1]): row for row in partial_rows}
    assert len(partial_by_event_id) == len(partial_rows)
    assert set(partial_by_event_id) == {window["event_id"] for window in partial_windows}
    for window in partial_windows:
        row = partial_by_event_id[window["event_id"]]
        row_symbols = [_unquote_markdown_code(symbol.strip()) for symbol in row[5].split(",")]
        assert _unquote_markdown_code(row[3]) == window["reason"]
        assert int(row[4]) == len(window["missing_symbols"])
        assert row_symbols == window["missing_symbols"]


def test_q001_inventory_reports_futures_options_and_gaps(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    npz_root = repo / "data" / "npz"
    npz_root.mkdir(parents=True)
    (repo / "packages" / "data_system" / "config").mkdir(parents=True)
    (repo / "runtime").mkdir(parents=True)
    monkeypatch.delenv("HFT3_NPZ_ROOT", raising=False)

    (npz_root / "manifest.json").write_text(
        json.dumps(
            [
                _manifest_row(npz_root / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz", "CPI_2024_09_11_TIGHT", "MES.v.0", 10),
                _manifest_row(npz_root / "ES.v.0_NFP_2024_10_04_TIGHT_mbo.npz", "NFP_2024_10_04_TIGHT", "ES.v.0", 20),
            ]
        ),
        encoding="utf-8",
    )
    events_csv = repo / "packages" / "data_system" / "config" / "events.csv"
    events_csv.write_text(
        "event_id,event_type,symbols\n"
        'CPI_2024_09_11_TIGHT,CPI,"MES.v.0,ES.v.0"\n',
        encoding="utf-8",
    )
    mbo_manifest = repo / "packages" / "data_system" / "config" / "mbo_pilot_basket_20260605_manifest.json"
    mbo_manifest.write_text(
        json.dumps(
            {
                "run_id": "mbo_pilot",
                "status": "completed_with_gaps",
                "databento_request": {
                    "dataset": "GLBX.MDP3",
                    "schema": "mbo",
                    "stype_in": "continuous",
                    "range_start_utc": "2024-01-01T00:00:00+00:00",
                    "range_end_utc": "2024-01-02T00:00:00+00:00",
                },
                "coverage": {
                    "expected_event_symbol_slots": 4,
                    "present_runnable_npz_slots": 3,
                    "missing_or_unavailable_slots": 1,
                },
                "partial_windows": [{"event_id": "FED_H41_2024_06_19_TIGHT"}],
                "no_market_windows": ["FOMC_PRESS_2024_09_15_TIGHT"],
            }
        ),
        encoding="utf-8",
    )
    data_doctor = repo / "runtime" / "data_doctor_report.json"
    data_doctor.write_text(
        json.dumps(
            {
                "run_utc": "2026-06-14T00:00:00+00:00",
                "failed": 0,
                "warned": 1,
                "checks": [{"name": "options-fixing-coverage", "status": "WARN", "detail": "gap_count=1"}],
                "options_lane": {
                    "fixing_mbo": {"dates_covered": 3, "first_date": "2026-01-01", "last_date": "2026-01-03"},
                    "expiry_coverage": {"gap_count": 1},
                },
            }
        ),
        encoding="utf-8",
    )

    report = build_q001_cme_data_inventory(repo_root=repo, verify_hashes=True)

    assert report["status"] == "INVENTORIED_WITH_WARNINGS"
    assert report["cme_symbol_universe"] == ["ES.v.0", "MES.v.0"]
    assert report["futures"]["active_npz_manifest"]["record_count"] == 2
    assert report["futures"]["mbo_pilot_basket"]["coverage_pct"] == 75.0
    assert report["options"]["options_lane"]["fixing_mbo"]["dates_covered"] == 3
    assert {gap["source"] for gap in report["gaps"]} == {"mbo_pilot_manifest", "data_doctor"}


def test_q001_inventory_honors_external_npz_root(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    external = tmp_path / "lake" / "npz"
    external.mkdir(parents=True)
    monkeypatch.setenv("HFT3_NPZ_ROOT", os.fspath(external))
    (external / "manifest.json").write_text(
        json.dumps([
            _manifest_row(
                external / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz",
                "CPI_2024_09_11_TIGHT",
                "MES.v.0",
            )
        ]),
        encoding="utf-8",
    )

    report = build_q001_cme_data_inventory(repo_root=repo)

    assert report["futures"]["active_npz_manifest"]["path"] == str(external / "manifest.json")
    assert report["futures"]["active_npz_manifest"]["record_count"] == 1
    assert any("sha256_content_not_verified" in gap["detail"] for gap in report["gaps"])
    assert any(gap["source"] == "mbo_pilot_manifest" for gap in report["gaps"])


def test_q001_inventory_blocks_on_manifest_schema_violation(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    npz_root = repo / "data" / "npz"
    npz_root.mkdir(parents=True)
    monkeypatch.delenv("HFT3_NPZ_ROOT", raising=False)
    (npz_root / "manifest.json").write_text(
        json.dumps([{"event_id": "CPI_2024_09_11_TIGHT", "symbol": "MES.v.0"}]),
        encoding="utf-8",
    )

    report = build_q001_cme_data_inventory(repo_root=repo, verify_hashes=True)

    assert report["status"] == "BLOCKED"
    active = report["futures"]["active_npz_manifest"]
    assert active["status"] == "FAIL_SCHEMA_OR_PATH_VALIDATION"
    assert active["missing_required_field_rows"] == 1
    assert any(gap["source"] == "active_npz_manifest" and gap["severity"] == "FAIL" for gap in report["gaps"])


def test_q001_inventory_blocks_on_manifest_sha_mismatch(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    npz_root = repo / "data" / "npz"
    npz_root.mkdir(parents=True)
    monkeypatch.delenv("HFT3_NPZ_ROOT", raising=False)
    row = _manifest_row(
        npz_root / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz",
        "CPI_2024_09_11_TIGHT",
        "MES.v.0",
    )
    row["sha256"] = "b" * 64
    (npz_root / "manifest.json").write_text(json.dumps([row]), encoding="utf-8")

    report = build_q001_cme_data_inventory(repo_root=repo, verify_hashes=True)

    assert report["status"] == "BLOCKED"
    active = report["futures"]["active_npz_manifest"]
    assert active["invalid_sha256_rows"] == 1
    assert "sha256 mismatch" in active["validation_error_examples"][0]


def test_q001_hash_verification_does_not_crash_on_missing_npz_path(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    npz_root = repo / "data" / "npz"
    npz_root.mkdir(parents=True)
    monkeypatch.delenv("HFT3_NPZ_ROOT", raising=False)
    row = _manifest_row(
        npz_root / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz",
        "CPI_2024_09_11_TIGHT",
        "MES.v.0",
    )
    (npz_root / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz").unlink()
    (npz_root / "manifest.json").write_text(json.dumps([row]), encoding="utf-8")

    report = build_q001_cme_data_inventory(repo_root=repo, verify_hashes=True)

    assert report["status"] == "BLOCKED"
    active = report["futures"]["active_npz_manifest"]
    assert active["missing_npz_files"] == 1


def test_q001_inventory_blocks_on_data_doctor_top_level_failures(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    npz_root = repo / "data" / "npz"
    npz_root.mkdir(parents=True)
    (repo / "runtime").mkdir(parents=True)
    monkeypatch.delenv("HFT3_NPZ_ROOT", raising=False)
    (npz_root / "manifest.json").write_text(
        json.dumps([
            _manifest_row(
                npz_root / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz",
                "CPI_2024_09_11_TIGHT",
                "MES.v.0",
            )
        ]),
        encoding="utf-8",
    )
    (repo / "runtime" / "data_doctor_report.json").write_text(
        json.dumps({"failed": 1, "warned": 0, "checks": []}),
        encoding="utf-8",
    )

    report = build_q001_cme_data_inventory(repo_root=repo)

    assert report["status"] == "BLOCKED"
    assert report["options"]["data_doctor_status"] == "FAIL"
    assert any(gap["source"] == "data_doctor" and gap["severity"] == "FAIL" for gap in report["gaps"])


def test_q001_inventory_reports_missing_events_csv_without_defaulting(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    npz_root = repo / "data" / "npz"
    npz_root.mkdir(parents=True)
    monkeypatch.delenv("HFT3_NPZ_ROOT", raising=False)
    (npz_root / "manifest.json").write_text(
        json.dumps([
            _manifest_row(
                npz_root / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz",
                "CPI_2024_09_11_TIGHT",
                "MES.v.0",
            )
        ]),
        encoding="utf-8",
    )

    report = build_q001_cme_data_inventory(repo_root=repo)

    assert report["cme_symbol_universe"] == []
    assert report["expected_canonical_cme_symbols"] == ["MES.v.0", "MNQ.v.0", "ES.v.0", "NQ.v.0", "ZN.v.0", "ZB.v.0", "RTY.v.0"]
    assert any(gap["source"] == "events_csv" and gap["detail"] == "MISSING" for gap in report["gaps"])


def test_q001_inventory_reports_malformed_json_without_crashing(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    npz_root = repo / "data" / "npz"
    npz_root.mkdir(parents=True)
    monkeypatch.delenv("HFT3_NPZ_ROOT", raising=False)
    (npz_root / "manifest.json").write_text("{bad", encoding="utf-8")

    report = build_q001_cme_data_inventory(repo_root=repo)

    assert report["status"] == "BLOCKED"
    assert report["futures"]["active_npz_manifest"]["status"] == "MALFORMED_JSON"
    assert any(gap["source"] == "active_npz_manifest" and gap["severity"] == "FAIL" for gap in report["gaps"])


def test_q001_inventory_blocks_on_incomplete_mbo_pilot_manifest(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    npz_root = repo / "data" / "npz"
    npz_root.mkdir(parents=True)
    (repo / "packages" / "data_system" / "config").mkdir(parents=True)
    monkeypatch.delenv("HFT3_NPZ_ROOT", raising=False)
    (npz_root / "manifest.json").write_text(
        json.dumps([
            _manifest_row(
                npz_root / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz",
                "CPI_2024_09_11_TIGHT",
                "MES.v.0",
            )
        ]),
        encoding="utf-8",
    )
    mbo_manifest = repo / "packages" / "data_system" / "config" / "mbo_pilot_basket_20260605_manifest.json"
    mbo_manifest.write_text(json.dumps({"status": "completed"}), encoding="utf-8")

    report = build_q001_cme_data_inventory(repo_root=repo)

    assert report["status"] == "BLOCKED"
    assert report["futures"]["mbo_pilot_basket"]["status"] == "MALFORMED_SCHEMA"
    assert any(gap["source"] == "mbo_pilot_manifest" and gap["severity"] == "FAIL" for gap in report["gaps"])


def test_q001_inventory_blocks_on_impossible_mbo_coverage_math(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    npz_root = repo / "data" / "npz"
    npz_root.mkdir(parents=True)
    (repo / "packages" / "data_system" / "config").mkdir(parents=True)
    monkeypatch.delenv("HFT3_NPZ_ROOT", raising=False)
    (npz_root / "manifest.json").write_text(
        json.dumps([
            _manifest_row(
                npz_root / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz",
                "CPI_2024_09_11_TIGHT",
                "MES.v.0",
            )
        ]),
        encoding="utf-8",
    )
    mbo_manifest = repo / "packages" / "data_system" / "config" / "mbo_pilot_basket_20260605_manifest.json"
    mbo_manifest.write_text(
        json.dumps(
            {
                "status": "completed",
                "databento_request": {
                    "dataset": "GLBX.MDP3",
                    "schema": "mbo",
                    "stype_in": "continuous",
                    "range_start_utc": "2024-01-01T00:00:00+00:00",
                    "range_end_utc": "2024-01-02T00:00:00+00:00",
                },
                "coverage": {
                    "expected_event_symbol_slots": 1,
                    "present_runnable_npz_slots": 2,
                    "missing_or_unavailable_slots": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    report = build_q001_cme_data_inventory(repo_root=repo)

    assert report["status"] == "BLOCKED"
    pilot = report["futures"]["mbo_pilot_basket"]
    assert pilot["status"] == "MALFORMED_SCHEMA"
    assert "coverage.present_runnable_npz_slots_gt_expected" in pilot["validation_errors"]
