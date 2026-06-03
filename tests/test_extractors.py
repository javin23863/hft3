"""Tests for the heuristic equation and table extractors (Phase 3 follow-up)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_pipeline.extractors import extract_equations, extract_tables
from research_pipeline.intake_bundle import is_quarantined, write_intake_bundle
from research_pipeline.intake_schema import (
    Assumption,
    DataRequirement,
    ExecutionLogic,
    ExperimentTranslationNotes,
    FeatureRequirement,
    ParameterRange,
    SignalLogic,
    TestableHypothesis,
    ThesisSummary,
)


# ---------- equation extraction ----------


def test_extract_inline_math() -> None:
    text = "Sharpe ratio $SR = \\frac{E[R - R_f]}{\\sigma}$ measures risk-adjusted return."
    eqs = extract_equations(text)
    assert len(eqs) == 1
    assert "Sharpe" in eqs[0].latex or "SR" in eqs[0].latex
    assert "frac" in eqs[0].latex or "sigma" in eqs[0].latex


def test_extract_display_math_dollar() -> None:
    text = "Value-at-Risk:\n\n$$VaR_{\\alpha} = \\inf\\{x : P(X \\leq x) \\geq \\alpha\\}$$\n\nDefined for confidence level $\\alpha$."
    eqs = extract_equations(text)
    # The display $$...$$ is one equation. The inline $\alpha$ is a
    # substring of the display, so dedup correctly keeps just one.
    assert len(eqs) == 1
    has_var = any("VaR" in e.latex for e in eqs)
    assert has_var


def test_extract_display_and_inline_dedup() -> None:
    """A display equation and a separate inline equation with different
    content produce two distinct equations."""
    text = (
        "$$y = mx + b$$\n"
        "and $z = ax^2 + bx + c$ are both quadratic."
    )
    eqs = extract_equations(text)
    assert len(eqs) == 2


def test_extract_display_math_bracket() -> None:
    text = "Sortino: \\[SR_{sortino} = \\frac{R - MAR}{\\sigma_{down}}\\] captures downside risk."
    eqs = extract_equations(text)
    assert any("SR" in e.latex and "sortino" in e.latex.lower() for e in eqs)


def test_extract_numbered_equation() -> None:
    text = (
        "The fundamental equation of microstructure is\n\n"
        "(1) p_t = p_{t-1} + \\Delta q_t + \\epsilon_t\n\n"
        "where \\epsilon is the innovation term."
    )
    eqs = extract_equations(text)
    assert any("p_t" in e.latex and "p_{t-1}" in e.latex for e in eqs)


def test_extract_dedupes() -> None:
    text = "$x = 1$ and again $x = 1$ and $x = 1$"
    eqs = extract_equations(text)
    assert len(eqs) == 1


def test_extract_filters_non_math() -> None:
    """A $...$ block with no math hint (no =, ∈, ∑, etc.) is rejected."""
    text = "Plain text $just words here$ nothing to see."
    eqs = extract_equations(text)
    assert eqs == []


def test_extract_assigns_unique_ids() -> None:
    text = "$a = 1$ and $b = 2$ and $c = 3$"
    eqs = extract_equations(text)
    ids = [e.eq_id for e in eqs]
    assert len(set(ids)) == len(ids) == 3


# ---------- table extraction ----------


def test_extract_markdown_table() -> None:
    text = (
        "Performance summary:\n\n"
        "| Strategy | Sharpe | MaxDD |\n"
        "| -------- | ------ | ----- |\n"
        "| HYP_1    | 1.20   | -0.05 |\n"
        "| HYP_5    | 0.80   | -0.10 |\n"
    )
    tables = extract_tables(text)
    assert len(tables) == 1
    t = tables[0]
    assert t.headers == ["Strategy", "Sharpe", "MaxDD"]
    assert len(t.rows) == 2
    assert t.rows[0][0] == "HYP_1"
    assert t.rows[1][1] == "0.80"


def test_extract_table_with_caption() -> None:
    text = (
        "Table 3: Backtest results across 5 regimes\n\n"
        "| Regime | NetPnL |\n"
        "| ------ | ------ |\n"
        "| vol    |  100   |\n"
        "| trend  |  -50   |\n"
    )
    tables = extract_tables(text)
    assert tables[0].caption.startswith("Backtest results across 5 regimes")


def test_extract_multiple_tables() -> None:
    text = (
        "| A | B |\n| - | - |\n| 1 | 2 |\n\n"
        "| C | D | E |\n| - | - | - |\n| 3 | 4 | 5 |\n| 6 | 7 | 8 |\n"
    )
    tables = extract_tables(text)
    assert len(tables) == 2
    assert tables[0].headers == ["A", "B"]
    assert tables[1].headers == ["C", "D", "E"]
    assert len(tables[1].rows) == 2


def test_extract_handles_misaligned_rows() -> None:
    """A row with fewer cells than headers is padded with empty strings."""
    text = (
        "| A | B | C |\n| - | - | - |\n| 1 | 2 |\n"
    )
    tables = extract_tables(text)
    assert tables[0].rows[0] == ["1", "2", ""]


def test_extract_no_table_returns_empty() -> None:
    text = "Just prose, no tables, no math $inline$ either."
    assert extract_tables(text) == []


# ---------- integration with write_intake_bundle ----------


def test_ingest_bundle_writes_equations_and_tables(tmp_path: Path) -> None:
    """The 14-file bundle now contains real equations and tables from the
    extractor (not always [])."""
    text = (
        "## Mean reversion on NQ after 9:30 stop runs\n\n"
        "Sharpe: $SR = \\frac{\\mu}{\\sigma}$.\n\n"
        "| Strategy | Sharpe |\n| -------- | ------ |\n| baseline | 0.5 |\n"
    )
    equations = extract_equations(text)
    tables = extract_tables(text)
    assert len(equations) >= 1
    assert len(tables) >= 1
    # Now write a bundle and verify the JSON files contain the data
    src = tmp_path / "dummy.md"
    src.write_text(text, encoding="utf-8")
    bundle = write_intake_bundle(
        research_id="r1",
        source_path=src,
        intake_dir=tmp_path / "intake_test",
        extracted_text=text,
        thesis_summary=ThesisSummary(
            main_thesis="Mean reversion on NQ after 9:30 stop runs " * 3,
            instrument_scope=["NQ"],
            time_horizon="intraday",
        ),
        assumptions=[],
        required_data=[],
        required_features=[],
        signal_logic=SignalLogic(signal="book_imbalance_top5 < -0.5", entry=["long"], exit=["30 bars"]),
        execution_logic=ExecutionLogic(order_types=["limit"], latency_budget_us=80),
        parameter_ranges=[ParameterRange(param="x", min=0.0, max=1.0, default=0.5)],
        failure_modes=[],
        testable_hypotheses=[
            TestableHypothesis(
                hypothesis_id="h1",
                statement="mean reversion works",
                pass_criteria="sharpe>0",
                fail_criteria="sharpe<0",
                metric="sharpe",
                threshold=0.0,
            )
        ],
        translation_notes=ExperimentTranslationNotes(
            missing_info=[], hft3_implementation_reqs=["test"]
        ),
        equations=equations,
        tables=tables,
    )
    eq_json = json.loads((bundle / "extracted_equations.json").read_text(encoding="utf-8"))
    tb_json = json.loads((bundle / "extracted_tables.json").read_text(encoding="utf-8"))
    assert len(eq_json) >= 1
    assert len(tb_json) >= 1
    assert "eq_id" in eq_json[0]
    assert "headers" in tb_json[0]
    # Bundle is NOT quarantined (valid thesis, valid signal, valid params)
    assert not is_quarantined(bundle)
