"""Tests for scripts/check_handoff_status.py."""
from __future__ import annotations

from scripts.check_handoff_status import validate_status_block


def test_valid_partial_handoff():
    text = """
merge-ready:     no
scope-green:     no (5 failed)
scope:           packages/options_lane/
verify-run:      python -m pytest tests/test_workbench/test_options_lane_campaign.py -q → exit 1
data-mode:       fixture
known-gaps:      PIT join audit pending
"""
    assert not validate_status_block(text)


def test_waived_requires_unverified_gaps():
    text = """
merge-ready:     no
scope-green:     not-run
scope:           workbench/
verify-run:      WAIVED (user: code-only)
data-mode:       n/a
known-gaps:      none
"""
    errs = validate_status_block(text)
    assert any("unverified" in e or "waived" in e for e in errs)


def test_merge_ready_yes_requires_green_verify():
    text = """
merge-ready:     yes
scope-green:     no
scope:           workbench/
verify-run:      pytest -q → exit 0
data-mode:       n/a
known-gaps:      none
"""
    errs = validate_status_block(text)
    assert any("scope-green" in e for e in errs)


def test_merge_ready_yes_ok():
    text = """
merge-ready:     yes
scope-green:     yes
scope:           workbench/
verify-run:      python -m pytest tests/test_workbench/ -q → exit 0
data-mode:       fixture
known-gaps:      none
"""
    assert not validate_status_block(text)
