"""Problem-only health check for the LLM slow-tier lane.

Implements the ``status`` subcommand (LLM_SLOW_TIER.md §10).

Output contract
---------------
- Zero problems detected  → print exactly "OK" to stdout, exit 0, write nothing.
- One or more problems    → print one line per problem to stdout, exit 1,
  write ``runtime/slow_tier/problems_latest.json`` and append to
  ``runtime/slow_tier/problems_history.jsonl``.

Problem checks (in order)
--------------------------
1. Missing label     — weekday (Mon–Fri) in the window that has a capture manifest
                       directory but no record in session_labels.jsonl.
2. Conflict-review   — any session in the window whose verifier_verdict == "conflict_review".
3. Capture problems  — from the most-recent manifest dir: gap_flags > 0, md_drops > 0,
                       reconnects > 2, unknown_symbol_drops > 0.
4. Stale capture     — most-recent manifest dir is older than 2 calendar days.
5. GDELT persistent  — gdelt_error noted in ALL label records of the last 3 sessions.
6. Intake quota      — >= weekly_quota candidates submitted this week (informational).
7. Review queue      — unreviewed entries present in golden/review_queue.jsonl.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import SlowTierConfig


# ---------------------------------------------------------------------------
# Individual check helpers
# ---------------------------------------------------------------------------

def _weekdays_in_window(window_start: date, window_end: date) -> List[date]:
    """Return Mon–Fri dates in [window_start, window_end] inclusive."""
    days: List[date] = []
    current = window_start
    while current <= window_end:
        if current.weekday() < 5:  # 0=Mon, 4=Fri
            days.append(current)
        current += timedelta(days=1)
    return days


def _load_session_labels(labels_path: Path) -> List[Dict[str, Any]]:
    """Load all records from session_labels.jsonl; return list in file order."""
    if not labels_path.is_file():
        return []
    records: List[Dict[str, Any]] = []
    try:
        for line in labels_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if isinstance(rec, dict):
                    records.append(rec)
            except json.JSONDecodeError:
                pass
    except OSError:
        pass
    return records


def _most_recent_manifest_dir(manifest_root: Path) -> Optional[Path]:
    """Return the most-recently-dated manifest subdirectory, or None."""
    if not manifest_root.is_dir():
        return None
    dated_dirs: List[tuple[str, Path]] = []
    for entry in manifest_root.iterdir():
        if entry.is_dir() and len(entry.name) == 10:
            dated_dirs.append((entry.name, entry))
    if not dated_dirs:
        return None
    dated_dirs.sort(key=lambda t: t[0], reverse=True)
    return dated_dirs[0][1]


# ---------------------------------------------------------------------------
# Check 1: Missing labels for weekdays with manifests present
# ---------------------------------------------------------------------------

def check_missing_labels(
    window_start: date,
    window_end: date,
    manifest_root: Path,
    labels_by_date: Dict[str, Any],
) -> List[str]:
    """Emit a problem for each weekday that has manifests but no label."""
    problems: List[str] = []
    for day in _weekdays_in_window(window_start, window_end):
        day_str = day.isoformat()
        day_manifest_dir = manifest_root / day_str
        if day_manifest_dir.is_dir():
            # Check that the directory has at least one manifest file
            has_manifests = any(day_manifest_dir.glob("*.json"))
            if has_manifests and day_str not in labels_by_date:
                problems.append(f"missing_label: {day_str} has manifests but no session label")
    return problems


# ---------------------------------------------------------------------------
# Check 2: Conflict-review verdicts in window
# ---------------------------------------------------------------------------

def check_conflict_reviews(
    window_start: date,
    window_end: date,
    all_records: List[Dict[str, Any]],
) -> List[str]:
    """Emit a problem for each session in the window with conflict_review verdict."""
    problems: List[str] = []
    for rec in all_records:
        trade_date_str = rec.get("trade_date", "")
        if not trade_date_str:
            continue
        try:
            trade_date = date.fromisoformat(trade_date_str)
        except ValueError:
            continue
        if not (window_start <= trade_date <= window_end):
            continue
        if rec.get("verifier_verdict") == "conflict_review":
            reasons = rec.get("verifier_reasons") or []
            reasons_str = "; ".join(str(r) for r in reasons[:3])
            problems.append(
                f"conflict_review: {trade_date_str} — reasons: {reasons_str}"
            )
    return problems


# ---------------------------------------------------------------------------
# Check 3: Capture problems from most-recent manifest dir
# ---------------------------------------------------------------------------

def check_capture_problems(manifest_root: Path) -> List[str]:
    """Inspect the most-recent manifest dir for quality issues."""
    problems: List[str] = []
    manifest_dir = _most_recent_manifest_dir(manifest_root)
    if manifest_dir is None:
        return problems

    date_str = manifest_dir.name
    total_gap_flags = 0
    total_md_drops = 0
    total_reconnects = 0
    total_unknown_drops = 0

    for path in manifest_dir.glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        total_gap_flags += int(raw.get("max_queue_gap_flag_count") or 0)
        total_md_drops += int(raw.get("md_drops_total") or 0)
        total_reconnects += int(raw.get("reconnects") or 0)
        total_unknown_drops += int(raw.get("unknown_symbol_drops") or 0)

    if total_gap_flags > 0:
        problems.append(
            f"capture_gap_flags: {date_str} total_gap_flags={total_gap_flags}"
        )
    if total_md_drops > 0:
        problems.append(
            f"capture_md_drops: {date_str} total_md_drops={total_md_drops}"
        )
    if total_reconnects > 2:
        problems.append(
            f"capture_reconnects: {date_str} total_reconnects={total_reconnects}"
        )
    if total_unknown_drops > 0:
        problems.append(
            f"capture_unknown_drops: {date_str} total_unknown_drops={total_unknown_drops}"
        )
    return problems


# ---------------------------------------------------------------------------
# Check 4: Stale capture
# ---------------------------------------------------------------------------

def check_stale_capture(manifest_root: Path, today: date) -> List[str]:
    """Emit a problem if the most-recent manifest dir is older than 2 calendar days."""
    manifest_dir = _most_recent_manifest_dir(manifest_root)
    if manifest_dir is None:
        return []
    try:
        dir_date = date.fromisoformat(manifest_dir.name)
    except ValueError:
        return []
    age_days = (today - dir_date).days
    if age_days > 2:
        return [f"stale_capture: most recent manifest is from {manifest_dir.name} ({age_days} days ago)"]
    return []


# ---------------------------------------------------------------------------
# Check 5: GDELT persistently failing
# ---------------------------------------------------------------------------

def check_gdelt_persistent_failure(all_records: List[Dict[str, Any]]) -> List[str]:
    """Emit a problem if gdelt_error is set in ALL of the last 3 session records."""
    # Take the 3 most recent records (last in file = most recent)
    recent = all_records[-3:] if len(all_records) >= 3 else all_records
    if len(recent) < 3:
        return []
    all_have_error = all(
        bool(rec.get("sources_summary", {}).get("gdelt_error"))
        for rec in recent
    )
    if all_have_error:
        dates = [rec.get("trade_date", "?") for rec in recent]
        return [f"gdelt_persistent_failure: gdelt_error in last {len(recent)} sessions ({', '.join(dates)})"]
    return []


# ---------------------------------------------------------------------------
# Check 6: Intake quota
# ---------------------------------------------------------------------------

def check_intake_quota(cfg: SlowTierConfig, today: date) -> List[str]:
    """Emit an informational problem if weekly intake quota is reached."""
    if not cfg.intake.enabled:
        return []
    weekly_quota = cfg.intake.weekly_quota
    # Look for intake output files in research_inputs/ (relative to CWD/repo root)
    # The intake writer uses research_inputs/{research_id}/ directories.
    # Count directories created in the trailing 7 days.
    intake_root = Path("research_inputs")
    if not intake_root.is_dir():
        return []
    week_start = today - timedelta(days=6)
    count = 0
    for entry in intake_root.iterdir():
        if not entry.is_dir():
            continue
        # Use directory mtime as a proxy for creation time
        try:
            mtime = date.fromtimestamp(entry.stat().st_mtime)
        except OSError:
            continue
        if mtime >= week_start:
            count += 1
    if count >= weekly_quota:
        return [f"intake_quota: {count} candidates this week (quota={weekly_quota})"]
    return []


# ---------------------------------------------------------------------------
# Check 7: Review queue
# ---------------------------------------------------------------------------

def check_review_queue(golden_dir: Path) -> List[str]:
    """Emit a problem if review_queue.jsonl has any entries."""
    queue_path = golden_dir / "review_queue.jsonl"
    if not queue_path.is_file():
        return []
    count = 0
    try:
        for line in queue_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                count += 1
    except OSError:
        return []
    if count > 0:
        return [f"review_queue: {count} unreviewed entries in golden/review_queue.jsonl"]
    return []


# ---------------------------------------------------------------------------
# Main status runner
# ---------------------------------------------------------------------------

def run_status(cfg: SlowTierConfig, days: int = 7) -> tuple[List[str], int]:
    """Run all problem checks.

    Returns (problems, exit_code).
    exit_code is 0 when problems is empty, 1 otherwise.
    """
    today = datetime.now(timezone.utc).date()
    window_end = today
    window_start = today - timedelta(days=days - 1)

    artifact_root = Path(cfg.artifact_root)
    manifest_root = artifact_root / cfg.manifest_subdir
    golden_dir = artifact_root / cfg.golden_subdir
    labels_path = artifact_root / "session_labels.jsonl"

    all_records = _load_session_labels(labels_path)
    labels_by_date = {
        rec["trade_date"]: rec
        for rec in all_records
        if rec.get("trade_date")
    }

    problems: List[str] = []

    problems.extend(check_missing_labels(window_start, window_end, manifest_root, labels_by_date))
    problems.extend(check_conflict_reviews(window_start, window_end, all_records))
    problems.extend(check_capture_problems(manifest_root))
    problems.extend(check_stale_capture(manifest_root, today))
    problems.extend(check_gdelt_persistent_failure(all_records))
    problems.extend(check_intake_quota(cfg, today))
    problems.extend(check_review_queue(golden_dir))

    if problems:
        _write_problems(problems, cfg)
        return problems, 1

    return [], 0


def _write_problems(problems: List[str], cfg: SlowTierConfig) -> None:
    """Write problems_latest.json and append to problems_history.jsonl."""
    runtime_dir = Path("runtime") / "slow_tier"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    generated_utc = datetime.now(timezone.utc).isoformat()
    doc: Dict[str, Any] = {
        "generated_utc": generated_utc,
        "n_problems": len(problems),
        "problems": problems,
    }

    # problems_latest.json — overwrite
    latest_path = runtime_dir / "problems_latest.json"
    latest_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    # problems_history.jsonl — append
    history_path = runtime_dir / "problems_history.jsonl"
    line = json.dumps(doc, ensure_ascii=False) + "\n"
    with open(history_path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
