"""Tests for scripts/check_handoff_status.py."""
from __future__ import annotations

from scripts.check_handoff_status import validate_status_block


def test_valid_partial_handoff():
    text = """
merge-ready:     no
scope-green:     no (5 failed)
scope:           packages/options_lane/
verify-run:      python -m pytest tests/test_workbench/test_options_lane_campaign.py -q → exit 1
plan-drift:      not-run
data-mode:       fixture
pr-ai-review:    unavailable(no-pr)
review-surface:  none(blocked: no PR yet)
known-gaps:      PIT join audit pending
"""
    assert not validate_status_block(text)


def test_waived_requires_unverified_gaps():
    text = """
merge-ready:     no
scope-green:     not-run
scope:           workbench/
verify-run:      WAIVED (user: code-only)
plan-drift:      not-run
data-mode:       n/a
pr-ai-review:    unavailable(no-pr)
review-surface:  none(blocked: verify waived)
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
plan-drift:      pass
data-mode:       n/a
pr-ai-review:    run
review-surface:  PR #1; head=abcdef0; split-needed no
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
plan-drift:      pass
data-mode:       fixture
pr-ai-review:    run
review-surface:  PR #1; head=abcdef0; split-needed no
known-gaps:      none
"""
    assert not validate_status_block(text)


def test_merge_ready_yes_requires_pr_ai_run():
    text = """
merge-ready:     yes
scope-green:     yes
scope:           workbench/
verify-run:      python -m pytest tests/test_workbench/ -q → exit 0
plan-drift:      pass
data-mode:       fixture
pr-ai-review:    unavailable(no-pr)
review-surface:  none(blocked: no PR yet)
known-gaps:      none
"""
    errs = validate_status_block(text)
    assert any("PR AI run evidence" in e for e in errs)


def test_missing_plan_drift_is_rejected():
    text = """
merge-ready:     no
scope-green:     yes
scope:           workbench/
verify-run:      python -m pytest tests/test_workbench/ -q → exit 0
data-mode:       fixture
pr-ai-review:    unavailable(no-pr)
review-surface:  none(blocked: no PR yet)
known-gaps:      plan drift key omitted
"""
    errs = validate_status_block(text)
    assert any("missing keys" in e and "plan-drift" in e for e in errs)


def test_merge_ready_yes_requires_plan_drift_pass():
    text = """
merge-ready:     yes
scope-green:     yes
scope:           workbench/
verify-run:      python -m pytest tests/test_workbench/ -q → exit 0
plan-drift:      not-run
data-mode:       fixture
pr-ai-review:    run
review-surface:  PR #1; head=abcdef0; split-needed no
known-gaps:      none
"""
    errs = validate_status_block(text)
    assert any("plan-drift: pass" in e for e in errs)


def test_plan_drift_value_must_be_exact():
    text = """
merge-ready:     yes
scope-green:     yes
scope:           workbench/
verify-run:      python -m pytest tests/test_workbench/ -q -> exit 0
plan-drift:      passed
data-mode:       fixture
pr-ai-review:    run
review-surface:  PR #1; head=abcdef0; split-needed no
known-gaps:      none
"""
    errs = validate_status_block(text)
    assert any("plan-drift must be pass, fail, or not-run" in e for e in errs)
    assert any("plan-drift: pass" in e for e in errs)


def test_pr_ai_run_requires_review_surface():
    text = """
merge-ready:     no
scope-green:     yes
scope:           workbench/
verify-run:      python -m pytest tests/test_workbench/ -q → exit 0
plan-drift:      pass
data-mode:       fixture
pr-ai-review:    run
review-surface:  none(blocked: no PR yet)
known-gaps:      PR AI status inconsistent with review surface
"""
    errs = validate_status_block(text)
    assert any("review surface id or URL plus head" in e for e in errs)


def test_merge_ready_yes_invalid_review_surface_reports_once():
    text = """
merge-ready:     yes
scope-green:     yes
scope:           workbench/
verify-run:      python -m pytest tests/test_workbench/ -q -> exit 0
plan-drift:      pass
data-mode:       fixture
pr-ai-review:    run
review-surface:  none(blocked: no PR yet)
known-gaps:      none
"""
    errs = validate_status_block(text)
    review_surface_errs = [e for e in errs if "review surface" in e]
    assert review_surface_errs == [
        "merge-ready: yes requires a current-head PR/MR/CL review surface"
    ]


def test_pr_ai_run_rejects_branch_only_surface():
    text = """
merge-ready:     no
scope-green:     yes
scope:           workbench/
verify-run:      python -m pytest tests/test_workbench/ -q → exit 0
plan-drift:      pass
data-mode:       fixture
pr-ai-review:    run
review-surface:  branch feat/workflow-fix; split-needed no
known-gaps:      PR not opened yet
"""
    errs = validate_status_block(text)
    assert any("review surface id or URL plus head" in e for e in errs)


def test_pr_ai_run_requires_head_sha():
    text = """
merge-ready:     no
scope-green:     yes
scope:           workbench/
verify-run:      python -m pytest tests/test_workbench/ -q → exit 0
plan-drift:      pass
data-mode:       fixture
pr-ai-review:    run
review-surface:  PR #1; split-needed no
known-gaps:      head SHA omitted
"""
    errs = validate_status_block(text)
    assert any("review surface id or URL plus head" in e for e in errs)


def test_pr_ai_run_rejects_sha_alias_for_head():
    text = """
merge-ready:     no
scope-green:     yes
scope:           workbench/
verify-run:      python -m pytest tests/test_workbench/ -q → exit 0
plan-drift:      pass
data-mode:       fixture
pr-ai-review:    run
review-surface:  PR #1; sha=abcdef0; split-needed no
known-gaps:      head field used wrong key
"""
    errs = validate_status_block(text)
    assert any("review surface id or URL plus head" in e for e in errs)


def test_pr_ai_run_rejects_numeric_head():
    text = """
merge-ready:     no
scope-green:     yes
scope:           workbench/
verify-run:      python -m pytest tests/test_workbench/ -q → exit 0
plan-drift:      pass
data-mode:       fixture
pr-ai-review:    run
review-surface:  PR #1; head=1; split-needed no
known-gaps:      head must be a commit SHA
"""
    errs = validate_status_block(text)
    assert any("review surface id or URL plus head" in e for e in errs)


def test_pr_ai_value_must_be_known():
    text = """
merge-ready:     no
scope-green:     yes
scope:           workbench/
verify-run:      python -m pytest tests/test_workbench/ -q → exit 0
plan-drift:      pass
data-mode:       fixture
pr-ai-review:    maybe-later
review-surface:  PR #1; head=abcdef0; split-needed no
known-gaps:      connector status malformed
"""
    errs = validate_status_block(text)
    assert any("pr-ai-review must be" in e for e in errs)


def test_pr_ai_pending_allowed_before_merge_ready():
    text = """
merge-ready:     no
scope-green:     yes
scope:           workbench/
verify-run:      python -m pytest tests/test_workbench/ -q -> exit 0
plan-drift:      pass
data-mode:       fixture
pr-ai-review:    pending
review-surface:  PR #1; head=abcdef0; split-needed no
known-gaps:      PR AI review still running
"""
    assert not validate_status_block(text)


def test_merge_ready_yes_rejects_pr_ai_pending():
    text = """
merge-ready:     yes
scope-green:     yes
scope:           workbench/
verify-run:      python -m pytest tests/test_workbench/ -q -> exit 0
plan-drift:      pass
data-mode:       fixture
pr-ai-review:    pending
review-surface:  PR #1; head=abcdef0; split-needed no
known-gaps:      none
"""
    errs = validate_status_block(text)
    assert any("PR AI run evidence" in e for e in errs)


def test_merge_ready_yes_allows_explicit_pr_ai_waiver():
    text = """
merge-ready:     yes
scope-green:     yes
scope:           workbench/
verify-run:      python -m pytest tests/test_workbench/ -q → exit 0
plan-drift:      pass
data-mode:       fixture
pr-ai-review:    waived-by-user
review-surface:  none(waived-by-user: owner accepted no external PR AI for this slice)
known-gaps:      none
"""
    assert not validate_status_block(text)


def test_pr_ai_waiver_requires_surface_waiver_receipt():
    text = """
merge-ready:     yes
scope-green:     yes
scope:           workbench/
verify-run:      python -m pytest tests/test_workbench/ -q → exit 0
plan-drift:      pass
data-mode:       fixture
pr-ai-review:    waived-by-user
review-surface:  none(blocked: no PR yet)
known-gaps:      none
"""
    errs = validate_status_block(text)
    assert any("waiver" in e for e in errs)


def test_pr_ai_waiver_rejects_malformed_surface_waiver():
    text = """
merge-ready:     yes
scope-green:     yes
scope:           workbench/
verify-run:      python -m pytest tests/test_workbench/ -q → exit 0
plan-drift:      pass
data-mode:       fixture
pr-ai-review:    waived-by-user
review-surface:  none(waived)
known-gaps:      none
"""
    errs = validate_status_block(text)
    assert any("none(waived-by-user:" in e for e in errs)
