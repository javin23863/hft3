"""Grounded symbolic gate — every violation cites a real PDF section.

Companion to `packages/data_layer/symbolic/latency_invariants.py` and
`docs/research/ONTOLOGY_CITATIONS.md`. These tests prove that the symbolic
gate never emits a violation without a citation back to the math-model PDF.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def test_grounded_flag_set_on_pass():
    from data_layer.symbolic.latency_invariants import check_latency_invariants

    result = check_latency_invariants({"latency_authority": {}, "per_trade_audit": []})
    assert result["passed"] is True
    assert result["grounded"] is True
    assert result["violation_cites"] == []


def test_every_violation_has_cite():
    from data_layer.symbolic.latency_invariants import check_latency_invariants

    packet = {
        "latency_authority": {
            "python_research_runtime_authoritative": True,
            "lane_pass": True,
            "latency_profitability_buffer_us": 0.0,
            "promote_candidate": True,
            "survives_cpp_execution_delay": False,
            "robustness_passed": False,
            "wfc_status": "FAIL",
        },
        "per_trade_audit": [
            {
                "market_data_exchange_ts": 200,
                "market_data_receive_ts": 100,
                "decision_end_ts": 90,
                "order_send_ts": 120,
                "fill_ts": 110,
                "feed_delay_us": 10.0,
                "decision_compute_us": 10.0,
                "decision_to_send_us": 10.0,
                "send_to_ack_us": 10.0,
                "tick_to_ack_us": 999.0,
            }
        ],
    }
    result = check_latency_invariants(packet)
    assert result["passed"] is False
    assert len(result["violations"]) == len(result["violation_cites"])
    assert len(result["violations"]) > 0


def test_cite_shape_is_complete():
    from data_layer.symbolic.latency_invariants import check_latency_invariants

    packet = {
        "latency_authority": {"python_research_runtime_authoritative": True},
        "per_trade_audit": [],
    }
    result = check_latency_invariants(packet)
    assert len(result["violation_cites"]) >= 1
    for v in result["violation_cites"]:
        assert set(v.keys()) >= {"message", "cite"}
        assert set(v["cite"].keys()) == {"pdf", "section", "page"}


def test_cite_pdf_resolves_on_disk():
    from data_layer.symbolic.latency_invariants import check_latency_invariants, _pdf_on_disk

    packet = {
        "latency_authority": {"python_research_runtime_authoritative": True},
        "per_trade_audit": [],
    }
    result = check_latency_invariants(packet)
    for v in result["violation_cites"]:
        pdf = v["cite"]["pdf"]
        assert (REPO / "docs" / "references" / pdf).is_file(), f"missing PDF on disk: {pdf}"
        assert _pdf_on_disk(pdf) is True


def test_cite_page_is_positive_int():
    from data_layer.symbolic.latency_invariants import check_latency_invariants

    packet = {
        "latency_authority": {"python_research_runtime_authoritative": True},
        "per_trade_audit": [],
    }
    result = check_latency_invariants(packet)
    for v in result["violation_cites"]:
        page = v["cite"]["page"]
        assert isinstance(page, int)
        assert page >= 1


def test_cite_section_non_empty():
    from data_layer.symbolic.latency_invariants import check_latency_invariants

    packet = {
        "latency_authority": {"python_research_runtime_authoritative": True},
        "per_trade_audit": [],
    }
    result = check_latency_invariants(packet)
    for v in result["violation_cites"]:
        section = v["cite"]["section"]
        assert isinstance(section, str)
        assert section.strip() != ""


def test_backward_compat_violations_still_strings():
    from data_layer.symbolic.latency_invariants import check_latency_invariants

    packet = {"latency_authority": {"python_research_runtime_authoritative": True}, "per_trade_audit": []}
    result = check_latency_invariants(packet)
    assert all(isinstance(v, str) for v in result["violations"])


def test_per_trade_chain_violation_cites_marked_point_process():
    from data_layer.symbolic.latency_invariants import check_latency_invariants

    packet = {
        "latency_authority": {},
        "per_trade_audit": [
            {
                "feed_delay_us": 10.0,
                "decision_compute_us": 10.0,
                "decision_to_send_us": 10.0,
                "send_to_ack_us": 10.0,
                "tick_to_ack_us": 999.0,
            }
        ],
    }
    result = check_latency_invariants(packet)
    chain_violations = [v for v in result["violation_cites"] if "tick_to_ack_us" in v["message"]]
    assert len(chain_violations) == 1
    cite = chain_violations[0]["cite"]
    assert cite["pdf"] == "chicago_cme_microstructure_mathematical_model.pdf"
    assert "MBO" in cite["section"] or "Marked" in cite["section"]


def test_exchange_receive_violation_cites_information_set():
    from data_layer.symbolic.latency_invariants import check_latency_invariants

    packet = {
        "latency_authority": {},
        "per_trade_audit": [
            {
                "market_data_exchange_ts": 200,
                "market_data_receive_ts": 100,
                "feed_delay_us": 0.0,
                "decision_compute_us": 0.0,
                "decision_to_send_us": 0.0,
                "send_to_ack_us": 0.0,
                "tick_to_ack_us": 0.0,
            }
        ],
    }
    result = check_latency_invariants(packet)
    exch_violations = [v for v in result["violation_cites"] if "market_data_receive_ts" in v["message"]]
    assert len(exch_violations) == 1
    cite = exch_violations[0]["cite"]
    assert cite["pdf"] == "chicago_cme_microstructure_mathematical_model.pdf"
    assert "Information set" in cite["section"] or "§1" in cite["section"]
    assert cite["page"] == 1
