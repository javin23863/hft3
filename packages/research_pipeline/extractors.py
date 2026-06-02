"""Heuristic equation and table extractors (Phase 3 follow-up).

These are simple, deterministic, regex-based extractors. They do not
require the LLM. For a research paper, the typical output is:
- 5–20 equations
- 3–10 tables
The extractors are intentionally conservative — false negatives are
acceptable; false positives pollute downstream HFT3 experiment specs.

For higher-fidelity extraction, the LLM is asked to disambiguate in a
second pass (`_INTAKE_SYSTEM` prompt). The extractors here are the
ground truth that the LLM is asked to confirm or correct.
"""
from __future__ import annotations

import re
from typing import List

from research_pipeline.intake_schema import Equation, Table

# --- Equations ---
# Match $...$ (inline math), $$...$$ (display math), and \[...\] (LaTeX display).
# We deliberately keep the regex simple — anything that looks like math
# is captured; the bundle writes whatever is found and lets the LLM
# disambiguate semantically in the structured pass.

_INLINE_MATH = re.compile(r"\$([^\$\n]{2,200})\$")
_DISPLAY_MATH_DOLLAR = re.compile(r"\$\$([^\$]{2,500})\$\$", re.MULTILINE)
_DISPLAY_MATH_BRACKET = re.compile(r"\\\[([\s\S]{2,500}?)\\\]")
_EQN_BLOCK = re.compile(r"^\s*(?:\(?\d+\)?[\.\)]\s*)(.+?)\s*$", re.MULTILINE)

# Lines that *look* like math: contain =, ∈, ∑, ∏, ∂, ∇, ∧, ∨, ≥, ≤, ≠, ±, ×, ÷
_MATH_HINT = re.compile(
    r"[=∈∑∏∂∇∧∨≥≤≠±×÷]|[\\](?:frac|sum|prod|int|sqrt|partial|nabla|sin|cos|tan|log|exp)"
)


def _find_equation_context(text: str, eq_start: int, window: int = 80) -> str:
    """Return the text just before `eq_start` (up to `window` chars) as
    the equation's `context`. Truncated at the previous newline."""
    pre = text[max(0, eq_start - window): eq_start]
    if "\n" in pre:
        pre = pre.rsplit("\n", 1)[-1]
    return pre.strip()


def _extract_variables(latex: str) -> List[str]:
    """Crude: pull out single-letter or Greek-letter identifiers that
    aren't LaTeX commands."""
    out: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"(?<![\\a-zA-Z])([a-zA-Z]|\\[a-zA-Z]+)", latex):
        v = m.group(0)
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def extract_equations(text: str) -> List[Equation]:
    """Return all equations detected in `text`. Dedupes by LaTeX body."""
    equations: list[Equation] = []
    seen: set[str] = set()
    eq_id_counter = 0

    def _add(latex: str, pos: int) -> None:
        nonlocal eq_id_counter
        latex = latex.strip()
        if not latex or len(latex) < 2:
            return
        # Skip pure-text matches (no math hint)
        if not _MATH_HINT.search(latex):
            return
        # Dedupe
        key = re.sub(r"\s+", "", latex)
        if key in seen:
            return
        seen.add(key)
        eq_id_counter += 1
        equations.append(Equation(
            eq_id=f"e{eq_id_counter}",
            latex=latex[:500],
            context=_find_equation_context(text, pos),
            variables=_extract_variables(latex)[:20],
        ))

    for m in _DISPLAY_MATH_DOLLAR.finditer(text):
        _add(m.group(1), m.start())
    for m in _DISPLAY_MATH_BRACKET.finditer(text):
        _add(m.group(1), m.start())
    for m in _INLINE_MATH.finditer(text):
        _add(m.group(1), m.start())
    # Numbered equations: "(1) y = mx + b" or "1. y = mx + b" at line start
    for m in _EQN_BLOCK.finditer(text):
        line = m.group(1).strip()
        if _MATH_HINT.search(line) and "=" in line and len(line) < 300:
            _add(line, m.start())
    return equations


# --- Tables ---
# A table in markdown looks like:
#   | col1 | col2 | col3 |
#   | ---- | ---- | ---- |
#   | a    | b    | c    |
# A table in plain text looks like 2+ rows of tab/space-separated cells
# that share column boundaries.

_MD_TABLE = re.compile(
    r"(?P<header>\|[^\n]+\|)[ \t]*\n\|[\s\-:|]+\|[ \t]*\n(?P<rows>(?:\|(?:[^\n\|]+\|)+[ \t]*(?:\n|$))+)",
    re.MULTILINE,
)


def _md_split_row(row: str) -> List[str]:
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    return cells


def _find_table_caption(text: str, table_start: int, window: int = 200) -> str:
    """Look backwards for "Table N: caption" or "Table N. caption"."""
    pre = text[max(0, table_start - window): table_start]
    m = re.search(
        r"(?im)^[\s]*(?:Table|Tab\.?)\s*\d+[:\.\s]+([^\n]{1,200})",
        pre,
    )
    if m:
        return m.group(1).strip()
    # Fallback: the last non-empty line before the table
    for line in reversed(pre.splitlines()):
        line = line.strip()
        if line and not line.startswith("|"):
            return line[:200]
    return ""


def extract_tables(text: str) -> List[Table]:
    """Return all markdown tables detected in `text`."""
    tables: list[Table] = []
    table_id_counter = 0
    for m in _MD_TABLE.finditer(text):
        header_line = m.group("header")
        rows_block = m.group("rows").strip()
        headers = _md_split_row(header_line)
        if not headers or all(not h for h in headers):
            continue
        rows: list[list[str]] = []
        for row_line in rows_block.splitlines():
            row = _md_split_row(row_line)
            # Pad / truncate to header width
            if len(row) < len(headers):
                row = row + [""] * (len(headers) - len(row))
            elif len(row) > len(headers):
                row = row[: len(headers)]
            rows.append(row)
        if not rows:
            continue
        table_id_counter += 1
        tables.append(Table(
            table_id=f"t{table_id_counter}",
            caption=_find_table_caption(text, m.start()),
            headers=headers,
            rows=rows,
        ))
    return tables
