"""Deterministic latency invariant checks (AlphaGeometry neuro-symbolic pattern).

Every emitted violation carries a cite back to a real section of
`docs/references/chicago_cme_microstructure_mathematical_model.pdf` so the
symbolic gate is grounded, not hand-waved. Citation table and rationale reside
in `docs/research/ONTOLOGY_CITATIONS.md`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, TypedDict

MATH_MODEL_PDF = "chicago_cme_microstructure_mathematical_model.pdf"


class Cite(TypedDict):
    pdf: str
    section: str
    page: int


class GroundedViolation(TypedDict):
    message: str
    cite: Cite


def _c(pdf: str, section: str, page: int) -> Cite:
    return {"pdf": pdf, "section": section, "page": page}


_CITES: Dict[str, Cite] = {
    "python_runtime_authority": _c(MATH_MODEL_PDF, "§19 Validation framework", 7),
    "lane_pass_buffer": _c(MATH_MODEL_PDF, "§19 Validation framework", 7),
    "promote_survives_cpp": _c(MATH_MODEL_PDF, "§4 MBO Marked Point Process", 2),
    "promote_robustness": _c(MATH_MODEL_PDF, "§19 Validation framework", 7),
    "promote_wfc": _c(MATH_MODEL_PDF, "§11 Dynamic control form", 4),
    "exchange_receive_order": _c(MATH_MODEL_PDF, "§1 Information set", 1),
    "decision_end_after_receive": _c(MATH_MODEL_PDF, "§11 Dynamic control form", 4),
    "fill_after_send": _c(MATH_MODEL_PDF, "§11 Dynamic control form", 4),
    "latency_chain_sum": _c(MATH_MODEL_PDF, "§4 MBO Marked Point Process", 2),
}

_DEFAULT_CITE: Cite = _c(MATH_MODEL_PDF, "§19 Validation framework", 7)


def _cite_for(key: str) -> Cite:
    return _CITES.get(key, _DEFAULT_CITE)


def _chain_tolerance_us() -> float:
    return 5.0


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _pdf_on_disk(pdf: str) -> bool:
    return (_repo_root() / "docs" / "references" / pdf).is_file()


def check_latency_invariants(packet: Dict[str, Any]) -> Dict[str, Any]:
    obligations: List[str] = []
    violations: List[str] = []
    cite_keys: List[str] = []

    lat = packet.get("latency_authority") or {}
    if lat.get("python_research_runtime_authoritative") is True:
        violations.append("python_research_runtime must not be authoritative")
        cite_keys.append("python_runtime_authority")

    if lat.get("lane_pass") is True:
        obligations.append("lane_pass => latency_profitability_buffer_us > 0")
        buf = lat.get("latency_profitability_buffer_us")
        if buf is None or float(buf) <= 0:
            violations.append("lane_pass true but latency_profitability_buffer_us <= 0")
            cite_keys.append("lane_pass_buffer")

    if lat.get("promote_candidate") is True:
        obligations.append("promote_candidate => survives_cpp_execution_delay and robustness_passed")
        if not lat.get("survives_cpp_execution_delay"):
            violations.append("promote_candidate true but survives_cpp_execution_delay false")
            cite_keys.append("promote_survives_cpp")
        if lat.get("robustness_passed") is not True:
            violations.append("promote_candidate true but robustness_passed not true")
            cite_keys.append("promote_robustness")
        wfc_status = lat.get("wfc_status")
        if wfc_status is not None and wfc_status != "PASS":
            violations.append(f"promote_candidate true but wfc_status is {wfc_status!r}, not PASS")
            cite_keys.append("promote_wfc")

    for i, trade in enumerate(packet.get("per_trade_audit") or []):
        obligations.append(f"trade[{i}]: market_data_exchange_ts_ns <= market_data_receive_ts_ns")
        exch = trade.get("market_data_exchange_ts")
        recv = trade.get("market_data_receive_ts")
        if exch is not None and recv is not None and int(recv) < int(exch):
            violations.append(f"trade[{i}]: market_data_receive_ts before market_data_exchange_ts")
            cite_keys.append("exchange_receive_order")

        obligations.append(f"trade[{i}]: decision_end_ts_ns >= market_data_receive_ts_ns")
        dend = trade.get("decision_end_ts")
        if recv is not None and dend is not None and int(dend) < int(recv):
            violations.append(f"trade[{i}]: decision_end_ts before market_data_receive_ts")
            cite_keys.append("decision_end_after_receive")

        obligations.append(f"trade[{i}]: fill_ts_ns >= order_send_ts_ns when fill present")
        send = trade.get("order_send_ts")
        fill = trade.get("fill_ts")
        if send is not None and fill is not None and int(fill) > 0 and int(send) > 0 and int(fill) < int(send):
            violations.append(f"trade[{i}]: fill_ts before order_send_ts")
            cite_keys.append("fill_after_send")

        fd = float(trade.get("feed_delay_us", 0))
        dc = float(trade.get("decision_compute_us", 0))
        dts = float(trade.get("decision_to_send_us", 0))
        sta = float(trade.get("send_to_ack_us", 0))
        tta = float(trade.get("tick_to_ack_us", 0))
        expected = fd + dc + dts + sta
        obligations.append(f"trade[{i}]: tick_to_ack_us ≈ feed + decision + send + ack")
        if abs(tta - expected) > _chain_tolerance_us():
            violations.append(
                f"trade[{i}]: tick_to_ack_us={tta:.1f} != chain sum {expected:.1f} (tol {_chain_tolerance_us()} µs)"
            )
            cite_keys.append("latency_chain_sum")

    if len(violations) != len(cite_keys):
        raise AssertionError(
            f"symbolic gate bug: {len(violations)} violations but {len(cite_keys)} cite keys; "
            "every violation must have a matching cite."
        )

    violation_cites: List[GroundedViolation] = [
        {"message": msg, "cite": _cite_for(key)} for msg, key in zip(violations, cite_keys)
    ]

    passed = len(violations) == 0
    return {
        "passed": passed,
        "obligations": obligations,
        "violations": violations,
        "grounded": True,
        "violation_cites": violation_cites,
    }
