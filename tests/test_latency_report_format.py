"""Tests for fixed latency report card format."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_PROBE_DIR = _REPO / "scripts" / "latency_probe"
if str(_PROBE_DIR) not in sys.path:
    sys.path.insert(0, str(_PROBE_DIR))

from report_format import build_report_card, render_console, render_markdown  # noqa: E402


def _sample_summary() -> dict:
    return {
        "run_id": "20260530T031754Z",
        "timestamp_utc": "2026-05-30T03:23:46+00:00",
        "authoritative_source": "chi404_bare_metal_only",
        "raw_dir": "runtime/latency_reports/raw/20260530T031754Z",
        "pass_criteria_path": "infrastructure/chi404/PASS_CRITERIA.json",
        "dominant_bottleneck": "network_path (worst p99 4094 us)",
        "network_p99_us": 4094.0,
        "network_p99_worst_source": "rithmic_tcp_65000",
        "network_limit_us": 500,
        "cyclictest": {"max_p99_us": 11, "limit_us": 20, "loaded_runs": 2, "gate_mode": "loaded_only"},
        "network": {
            "gateway_ping": {"host": "64.44.98.129", "status": "ok", "p99_ms": 0.193, "loss_pct": 0.0},
            "rithmic_tcp_65000": {"host": "ritpz.example", "status": "ok", "p99_ms": 4.094},
        },
        "gates": {"cyclictest_pass": True, "network_pass": False, "order_ack_pass": None},
        "recommended_lane": {
            "lane": None,
            "lane_name": "undetermined",
            "partial": True,
            "infrastructure_meets_lane_1": False,
            "note": "R|API+ not wired",
        },
        "rithmic_app_latency": {"status": "BLOCKED", "reason": "R|API+ not wired"},
        "e2e_harness": {"status": "BLOCKED", "reason": "R|API+ not wired"},
        "trial_order_ack_appendix": {
            "status": "missing",
            "profile_path": "reports/rithmic_trial/2026-05-30/latency_profile.json",
            "reason": "No order ack events captured yet",
            "populate_hint": "Send paper orders in R|Trader VM, then bash scripts/chi404_run_trial_live.sh",
            "note": "Does not affect colo recommended_lane",
        },
        "strategy_guidance": {
            "realistic": ["Diagnose gateway/Rithmic path before colo execution assumptions."],
            "unrealistic": ["Tier-0.5 ms production tier with worst network p99 above PASS limit."],
        },
    }


def test_report_card_fixed_sections() -> None:
    card = build_report_card(_sample_summary())
    assert card["schema_version"] == "1"
    assert card["verdict"]["overall"] == "FAIL"
    assert len(card["colo_gates"]) == 3
    assert "network_probes" in card
    assert card["trial_appendix"]["status"] == "MISSING"


def test_render_markdown_has_numbered_sections() -> None:
    summary = _sample_summary()
    card = build_report_card(summary)
    md = render_markdown(summary, card)
    for section in (
        "## 1. Run",
        "## 2. Verdict",
        "## 3. Colo gates (authoritative)",
        "## 4. Colo lane",
        "## 5. Network probes (detail)",
        "## 6. Trial order ack (non-production appendix)",
        "## 7. Blocked (R|API+)",
        "## 8. Strategy guidance",
    ):
        assert section in md


def test_render_console_stable_header() -> None:
    card = build_report_card(_sample_summary())
    text = render_console(card)
    assert "CHI404 LATENCY REPORT" in text
    assert "COLO GATES (authoritative)" in text
    assert "TRIAL ORDER ACK (non-production appendix)" in text
