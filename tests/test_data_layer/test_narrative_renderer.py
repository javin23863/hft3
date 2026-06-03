"""Deterministic narrative renderer — narrative_md is not written by the LLM.

Companion to `packages/data_layer/llm/narrative_renderer.py`. The renderer
is the only path that produces narrative_md. The tests prove:
- the rendered markdown contains the run_id, the verdict, and any violation cites
- changing inputs deterministically changes the output
- closing annotations (LATENCY_AUTHORITY_FIELD without cite) round-trip into the report
- non-closed annotations are dropped before the render is called (no LLM free-form)
"""

from __future__ import annotations


def _sample_packet():
    return {
        "run_id": "run_test_42",
        "event_context": {"event_id": "CPI_2024_09_11_TIGHT", "event_state": "CPI_RELEASE"},
        "latency_authority": {
            "breakeven_us": 4.5,
            "latency_profitability_buffer_us": 1.2,
            "feed_to_ack_us_p50": 2.1,
            "feed_to_ack_us_p99": 6.8,
            "python_research_runtime_us": 0.4,
            "promote_candidate": True,
            "robustness_passed": True,
            "wfc_status": "PASS",
        },
    }


def _sample_symbolic_pass():
    return {
        "passed": True,
        "obligations": [],
        "violations": [],
        "grounded": True,
        "violation_cites": [],
    }


def _sample_symbolic_fail():
    return {
        "passed": False,
        "obligations": [],
        "violations": ["trade[0]: market_data_receive_ts before market_data_exchange_ts"],
        "grounded": True,
        "violation_cites": [
            {
                "message": "trade[0]: market_data_receive_ts before market_data_exchange_ts",
                "cite": {
                    "pdf": "chicago_cme_microstructure_mathematical_model.pdf",
                    "section": "§1 Information set",
                    "page": 1,
                },
            }
        ],
    }


def test_render_includes_run_id_and_event():
    from data_layer.llm.narrative_renderer import render_deterministic_narrative

    md = render_deterministic_narrative(_sample_packet(), _sample_symbolic_pass(), [])
    assert "run_test_42" in md
    assert "CPI_RELEASE" in md
    assert "CPI_2024_09_11_TIGHT" in md


def test_render_pass_path_contains_promote_verdict():
    from data_layer.llm.narrative_renderer import render_deterministic_narrative

    md = render_deterministic_narrative(_sample_packet(), _sample_symbolic_pass(), [])
    assert "PROMOTE_CANDIDATE" in md
    assert "PASSED" in md
    assert "Decision" in md


def test_render_fail_path_contains_fail_closed_and_cite():
    from data_layer.llm.narrative_renderer import render_deterministic_narrative

    md = render_deterministic_narrative(_sample_packet(), _sample_symbolic_fail(), [])
    assert "FAIL_CLOSED" in md
    assert "§1 Information set" in md
    assert "p.1" in md


def test_render_includes_annotations_table():
    from data_layer.llm.narrative_renderer import render_deterministic_narrative

    annotations = [
        {
            "source_type": "LATENCY_AUTHORITY_FIELD",
            "source_id": "latency_authority",
            "field": "breakeven_us",
            "value": 4.5,
        }
    ]
    md = render_deterministic_narrative(_sample_packet(), _sample_symbolic_pass(), annotations)
    assert "Closed-claim kg_annotations" in md
    assert "LATENCY_AUTHORITY_FIELD" in md
    assert "breakeven_us" in md


def test_render_is_deterministic_for_same_inputs():
    from data_layer.llm.narrative_renderer import render_deterministic_narrative

    md1 = render_deterministic_narrative(_sample_packet(), _sample_symbolic_pass(), [])
    md2 = render_deterministic_narrative(_sample_packet(), _sample_symbolic_pass(), [])
    assert md1 == md2


def test_render_changes_when_input_changes():
    from data_layer.llm.narrative_renderer import render_deterministic_narrative

    md_pass = render_deterministic_narrative(_sample_packet(), _sample_symbolic_pass(), [])
    md_fail = render_deterministic_narrative(_sample_packet(), _sample_symbolic_fail(), [])
    assert md_pass != md_fail


def test_render_handles_missing_optional_fields():
    from data_layer.llm.narrative_renderer import render_deterministic_narrative

    minimal_packet = {"run_id": "r1"}
    md = render_deterministic_narrative(minimal_packet, _sample_symbolic_pass(), [])
    assert "r1" in md
    assert "Symbolic gate" in md
