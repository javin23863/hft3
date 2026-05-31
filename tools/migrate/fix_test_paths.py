#!/usr/bin/env python3
"""Update test paths for apps/packages/artifacts layout."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REPLACEMENTS = [
    ('REPO / "workbench"', 'REPO / "apps" / "workbench"'),
    ('REPO / "research_cards"', 'REPO / "artifacts" / "research_cards"'),
    ('_REPO / "data_system"', '_REPO / "packages" / "data_system"'),
    ('_REPO / "workbench"', '_REPO / "apps" / "workbench"'),
    ('parents[2] / "workbench"', 'parents[2] / "apps" / "workbench"'),
    ('tmp_path / "research_cards"', 'tmp_path / "artifacts" / "research_cards"'),
    ('tmp_repo / "research_cards"', 'tmp_repo / "artifacts" / "research_cards"'),
]

count = 0
for path in (REPO / "tests").rglob("*.py"):
    text = path.read_text(encoding="utf-8")
    orig = text
    for old, new in REPLACEMENTS:
        text = text.replace(old, new)
    if text != orig:
        path.write_text(text, encoding="utf-8")
        count += 1
        print(path.relative_to(REPO))
print(f"Updated {count} files")
