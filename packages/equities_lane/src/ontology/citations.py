"""Track ontology claim ids and PDF citations. Ungrounded claims are rejected.

Citation sources tracked here:
- low_float_momentum_anomaly_research_pack.pdf (PARABOLIC_LOW_FLOAT_DECADAL.md)
- hft3 source-of-truth code in ``packages/equities_lane/src/``
- OpenFoundry ``domain-packs/core/pack.yaml`` stream ``backtest-run``
"""
from __future__ import annotations

from typing import Iterable


_PDF_RESEARCH_PACK = "low_float_momentum_anomaly_research_pack.pdf"
_OPENFOUNDRY_DOMAIN_PACK = "domain-packs/core/pack.yaml"


def cite_claim(*, claim_id: str, pdf: str | None = None, code_ref: str | None = None) -> dict:
    """Build a citation record. Must include a claim_id and at least one anchor."""
    if not claim_id:
        raise ValueError("claim_id is required")
    if not pdf and not code_ref:
        raise ValueError(f"claim {claim_id!r} has no pdf or code_ref anchor")
    return {
        "claim_id": claim_id,
        "pdf": pdf,
        "code_ref": code_ref,
    }


def require_grounding(
    citations: Iterable[dict],
    *,
    context: str,
) -> None:
    """Reject ungrounded claims.

    Raises ValueError if any citation is missing claim_id, pdf, or code_ref.
    Empty citation list is also rejected.
    """
    citations = list(citations)
    if not citations:
        raise ValueError(f"{context}: route decision has zero ontology citations")
    for c in citations:
        if "claim_id" not in c or not c["claim_id"]:
            raise ValueError(f"{context}: citation missing claim_id")
        if not c.get("pdf") and not c.get("code_ref"):
            raise ValueError(f"{context}: claim {c.get('claim_id')!r} has no pdf or code_ref")


def default_citations_for_route(route: str) -> list[dict]:
    """Standard citations used by every route decision record."""
    base = [
        cite_claim(
            claim_id="equities_l3_only_policy",
            pdf=None,
            code_ref="packages/equities_lane/src/l3_policy.py::require_l3_session",
        ),
        cite_claim(
            claim_id="equities_universe_config",
            pdf=None,
            code_ref="packages/equities_lane/config/universe.yaml",
        ),
        cite_claim(
            claim_id="opra_options_data_map",
            pdf=None,
            code_ref="docs/research/EQUITY_OPTIONS_DATA_MAP.md",
        ),
    ]
    if route == "OPTION_ONLY":
        base.append(
            cite_claim(
                claim_id="black_scholes_greeks",
                pdf=_PDF_RESEARCH_PACK,
                code_ref="packages/equities_lane/src/options/chain_loader.py",
            )
        )
    elif route == "STOCK_AND_OPTION":
        base.append(
            cite_claim(
                claim_id="combo_synergy_threshold",
                pdf=_PDF_RESEARCH_PACK,
                code_ref="packages/equities_lane/src/route/comparator.py::compare_routes",
            )
        )
    elif route == "NO_TRADE":
        base.append(
            cite_claim(
                claim_id="no_trade_risk_control",
                pdf=_PDF_RESEARCH_PACK,
                code_ref="packages/equities_lane/src/route/comparator.py::_route_reasons",
            )
        )
    return base
