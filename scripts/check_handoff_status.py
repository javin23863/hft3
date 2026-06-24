#!/usr/bin/env python3
"""Validate agent handoff status blocks per docs/VALIDATION_HONESTY.md."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REQUIRED_KEYS = (
    "merge-ready",
    "scope-green",
    "scope",
    "verify-run",
    "plan-drift",
    "data-mode",
    "pr-ai-review",
    "review-surface",
    "known-gaps",
)

_FORBIDDEN_WHEN_NOT_GREEN = (
    re.compile(r"\bmerge-ready:\s*yes\b", re.I),
    re.compile(r"\ball todos complete\b", re.I),
    re.compile(r"\bshipped per plan\b", re.I),
)
_VALID_PR_AI = {
    "pending",
    "run",
    "waived-by-user",
}
_UNAVAILABLE_PR_AI = re.compile(r"unavailable\([^)\s][^)]*\)\Z", re.I)
_REVIEW_SURFACE_ID = re.compile(
    r"\b(?:pr|mr|cl)\s*(?:#|!|:)?\s*\d+\b"
    r"|https?://\S+/(?:pull|pulls|merge_requests|-/merge_requests|reviews|changes)/\d+",
    re.I,
)
_REVIEW_SURFACE_HEAD = re.compile(
    r"\bhead=[0-9a-f]{7,40}\b",
    re.I,
)

_WAIVED = re.compile(r"WAIVED\s*\(\s*user", re.I)
_EXIT_OK = re.compile(r"(?:→|exit\s+code\s*:?)\s*0\b|exit\s+0\b", re.I)


def _has_current_review_surface(value: str) -> bool:
    return bool(_REVIEW_SURFACE_ID.search(value) and _REVIEW_SURFACE_HEAD.search(value))


def _has_waived_review_surface(value: str) -> bool:
    return bool(re.fullmatch(r"none\(waived-by-user:\s*\S.*\)", value.strip(), re.I))


def parse_status_block(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for key in _REQUIRED_KEYS:
            prefix = f"{key}:"
            if line.lower().startswith(prefix):
                out[key] = line.split(":", 1)[1].strip()
    return out


def validate_status_block(text: str, *, strict_merge: bool = True) -> list[str]:
    errors: list[str] = []
    block = parse_status_block(text)
    missing = [k for k in _REQUIRED_KEYS if k not in block]
    if missing:
        errors.append(f"missing keys: {', '.join(missing)}")
        return errors

    merge = block["merge-ready"].lower()
    scope = block["scope-green"].lower()
    verify = block["verify-run"]
    plan_drift = block["plan-drift"].lower()
    pr_ai = block["pr-ai-review"].lower()
    review_surface = block["review-surface"].lower()
    gaps = block["known-gaps"].lower()

    if merge not in {"yes", "no"}:
        errors.append(f"merge-ready must be yes|no, got {block['merge-ready']!r}")

    if plan_drift not in {"pass", "fail", "not-run"}:
        errors.append(
            "plan-drift must be pass, fail, or not-run"
        )

    if pr_ai not in _VALID_PR_AI and not _UNAVAILABLE_PR_AI.fullmatch(pr_ai):
        errors.append(
            "pr-ai-review must be pending, run, "
            "unavailable(<reason>), or waived-by-user"
        )
    if (
        pr_ai == "run"
        and not _has_current_review_surface(review_surface)
        and not (merge == "yes" and strict_merge)
    ):
        errors.append(
            "pr-ai-review: run requires a PR/MR/CL review surface id or URL plus head=<sha>"
        )
    if pr_ai == "waived-by-user" and not _has_waived_review_surface(review_surface):
        errors.append(
            "pr-ai-review: waived-by-user requires review-surface: none(waived-by-user: <reason>)"
        )

    waived = bool(_WAIVED.search(verify))
    scope_yes = scope.startswith("yes")
    scope_not_run = scope.startswith("not-run") or scope == "not-run"

    if waived and merge == "yes":
        errors.append("merge-ready: yes forbidden when verify-run is WAIVED (user)")

    if merge == "yes" and strict_merge:
        if not scope_yes:
            errors.append("merge-ready: yes requires scope-green: yes")
        if waived:
            errors.append("merge-ready: yes requires verify-run with exit 0, not WAIVED")
        elif not _EXIT_OK.search(verify):
            errors.append("merge-ready: yes requires verify-run to show exit 0 / passed")
        if plan_drift != "pass":
            errors.append("merge-ready: yes requires plan-drift: pass")
        if pr_ai not in {"run", "waived-by-user"}:
            errors.append("merge-ready: yes requires PR AI run evidence or explicit owner waiver")
        if pr_ai == "run" and not _has_current_review_surface(review_surface):
            errors.append("merge-ready: yes requires a current-head PR/MR/CL review surface")
        if pr_ai == "waived-by-user" and not _has_waived_review_surface(review_surface):
            errors.append("merge-ready: yes with PR AI waiver requires explicit review-surface waiver")

    if gaps in {"none", "none declared", "none."} and not scope_yes:
        errors.append(
            "known-gaps: none requires scope-green: yes and no open lane addendum items"
        )

    if waived and gaps in {"none", "none declared", "none."}:
        errors.append("known-gaps must be unverified (verify waived) or list gaps when verify waived")

    if not scope_yes and not scope_not_run:
        for pat in _FORBIDDEN_WHEN_NOT_GREEN:
            if pat.search(text):
                errors.append(f"forbidden claim in handoff while scope-green is not yes: {pat.pattern}")
                break

    return errors


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "file",
        nargs="?",
        type=Path,
        help="Handoff text file (default: stdin)",
    )
    p.add_argument(
        "--require",
        action="store_true",
        help="Exit 1 if file missing (for CI when HANDOFF_STATUS_FILE set)",
    )
    args = p.parse_args(argv)

    if args.file is None:
        text = sys.stdin.read()
    elif not args.file.is_file():
        if args.require:
            print(f"handoff file not found: {args.file}", file=sys.stderr)
            return 1
        print("skip: no handoff file")
        return 0
    else:
        text = args.file.read_text(encoding="utf-8")

    if not text.strip():
        if args.require:
            print("empty handoff status block", file=sys.stderr)
            return 1
        return 0

    errors = validate_status_block(text)
    if errors:
        for e in errors:
            print(f"handoff-status: {e}", file=sys.stderr)
        return 1
    print("handoff-status: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
