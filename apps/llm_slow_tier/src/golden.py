"""Golden-set loader, auto-seeder, and eval harness for the LLM slow-tier lane.

Golden files are stored as individual JSON files in:
    artifacts/research_cards/slow_tier/golden/{date}.json

Each file has the schema:
    {"date": "YYYY-MM-DD", "label": "<label>", "notes": "<optional string>"}

Auto-seeded files additionally carry:
    {"date": "...", "label": "...", "source": "auto_corroborated", "evidence": [...]}

Human-authored files always take precedence: auto-seeding NEVER overwrites a file
whose "source" field is absent or differs from "auto_corroborated".

The eval runner re-labels each golden date by replaying the labeler against
stored digests (loaded from stored model outputs in session_labels.jsonl, or
by re-running digest+label if no stored output exists).  It then computes:
  - agreement rate: fraction of dates where final_label == golden_label
  - conflict_rate: fraction of dates where verifier_verdict == "conflict_review"

Gate (LLM_SLOW_TIER.md §7):
  gate_pass = (agreement >= 0.90) AND (conflict_rate < 0.10)

Output is written to runtime/validation/slow_tier_eval.json.

Auto-seeding rules (LLM_SLOW_TIER.md §7, auto-corroboration):
  (a) label==macro_event AND >=1 calendar hit → corroborated
  (b) label==war_escalation AND >=1 symbol z >= z_stress AND >=1 GDELT headline → corroborated
  (c) label==calm AND all z in [-z_quiet, +z_quiet] AND total_gap_flags==0 AND
      total_reconnects==0 AND no symbols in insufficient_baseline for >=half the symbols → corroborated
  (d) everything else → NOT corroborated; if verdict==conflict_review → review queue
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import SlowTierConfig

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Golden file loader
# ---------------------------------------------------------------------------

def load_golden_set(golden_dir: Path) -> List[Dict[str, Any]]:
    """Load all golden hand-labeled files from golden_dir.

    Returns a list of dicts with keys: date, label, notes.
    Files that fail to parse are silently skipped.
    """
    if not golden_dir.is_dir():
        return []

    entries: List[Dict[str, Any]] = []
    for path in sorted(golden_dir.glob("*.json")):
        if path.name == "corrections.jsonl":
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        date = raw.get("date", "")
        label = raw.get("label", "")
        if not date or not label:
            continue
        entries.append({
            "date": str(date),
            "label": str(label),
            "notes": str(raw.get("notes") or ""),
        })
    return entries


# ---------------------------------------------------------------------------
# Session-labels lookup (to avoid re-running the full labeler during eval)
# ---------------------------------------------------------------------------

def _load_session_labels(labels_path: Path) -> Dict[str, Dict[str, Any]]:
    """Return {trade_date -> last record} from session_labels.jsonl."""
    if not labels_path.is_file():
        return {}

    by_date: Dict[str, Dict[str, Any]] = {}
    try:
        lines = labels_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("trade_date"):
            by_date[rec["trade_date"]] = rec  # last record wins

    return by_date


# ---------------------------------------------------------------------------
# Per-date evaluation
# ---------------------------------------------------------------------------

def _eval_one_date(
    golden_entry: Dict[str, Any],
    stored_labels: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Evaluate a single golden date.

    If a stored label exists for the date, use its final_label and
    verifier_verdict directly.  Otherwise record as 'missing_stored_label'.
    """
    date = golden_entry["date"]
    golden_label = golden_entry["label"]

    stored = stored_labels.get(date)
    if stored is None:
        return {
            "date": date,
            "golden_label": golden_label,
            "model_label": None,
            "final_label": None,
            "verifier_verdict": None,
            "agree": False,
            "note": "missing_stored_label",
        }

    final_label = stored.get("label")
    verifier_verdict = stored.get("verifier_verdict")

    agree = final_label == golden_label

    return {
        "date": date,
        "golden_label": golden_label,
        "model_label": stored.get("label"),  # after verifier
        "final_label": final_label,
        "verifier_verdict": verifier_verdict,
        "agree": agree,
    }


# ---------------------------------------------------------------------------
# Auto-corroboration logic
# ---------------------------------------------------------------------------

def _check_corroboration(
    label: str,
    digest: Any,           # Digest dataclass; avoid circular import — duck-typed
    sources_summary: Dict[str, Any],
    cfg: SlowTierConfig,
) -> tuple[bool, str, list[str]]:
    """Evaluate auto-corroboration rules (a)–(d).

    Returns (corroborated, rule_fired, evidence_strings).
    evidence_strings carry the key numbers that triggered the rule.
    """
    calendar_hits: List[str] = sources_summary.get("calendar_hits") or []
    gdelt_records: List[Any] = sources_summary.get("gdelt_top_events") or []
    zscores: Dict[str, float] = getattr(digest, "trade_count_zscores", {}) or {}
    insufficient: List[str] = getattr(digest, "insufficient_baseline", []) or []
    n_symbols: int = getattr(digest, "n_symbols", 0)
    total_gap_flags: int = getattr(digest, "total_gap_flags", 0)
    total_reconnects: int = getattr(digest, "total_reconnects", 0)

    # Rule (a): macro_event + calendar hit
    if label == "macro_event" and len(calendar_hits) >= 1:
        evidence = [
            f"rule_a: macro_event with {len(calendar_hits)} calendar hit(s)",
            f"calendar_hits: {calendar_hits[:5]}",
        ]
        return True, "a", evidence

    # Rule (b): war_escalation + z >= z_stress + GDELT present
    if label == "war_escalation":
        high_z_symbols = [
            sym for sym, z in zscores.items() if abs(z) >= cfg.z_stress
        ]
        if len(high_z_symbols) >= 1 and len(gdelt_records) >= 1:
            evidence = [
                f"rule_b: war_escalation with {len(high_z_symbols)} symbol(s) z>={cfg.z_stress}",
                f"high_z_symbols: {high_z_symbols}",
                f"gdelt_records: {len(gdelt_records)}",
            ]
            return True, "b", evidence

    # Rule (c): calm + all z in [-z_quiet, +z_quiet] + zero gap/reconnects +
    #           baseline sufficient for >=half the symbols
    if label == "calm":
        all_z_quiet = all(abs(z) <= cfg.z_quiet for z in zscores.values()) if zscores else True
        no_gaps = (total_gap_flags == 0)
        no_reconnects = (total_reconnects == 0)
        # "baseline sufficient for >= half the symbols" means
        # insufficient_baseline count <= half of n_symbols
        n_insufficient = len(insufficient)
        baseline_ok = (n_symbols == 0) or (n_insufficient <= n_symbols / 2)
        if all_z_quiet and no_gaps and no_reconnects and baseline_ok:
            evidence = [
                f"rule_c: calm — all z in [-{cfg.z_quiet}, +{cfg.z_quiet}]",
                f"total_gap_flags={total_gap_flags}, total_reconnects={total_reconnects}",
                f"n_symbols={n_symbols}, n_insufficient_baseline={n_insufficient}",
            ]
            return True, "c", evidence

    # Rule (d): not corroborated
    return False, "d", []


# ---------------------------------------------------------------------------
# Auto-seeder (called after each nightly-label write)
# ---------------------------------------------------------------------------

def maybe_auto_seed_golden(
    *,
    trade_date: str,
    label: str,
    verifier_verdict: str,
    verifier_reasons: List[str],
    digest: Any,
    sources_summary: Dict[str, Any],
    cfg: SlowTierConfig,
) -> Optional[str]:
    """Attempt to auto-seed a golden file for trade_date.

    Called after a label record is written.  Only acts when:
      - verdict == "accept" AND corroboration rule (a)/(b)/(c) fires
        → writes golden/{date}.json with source="auto_corroborated"
      - verdict == "conflict_review"
        → appends golden/review_queue.jsonl

    Returns a short status string for logging:
      "seeded", "skipped_human_senior", "review_queued", "not_corroborated",
      or "conflict_queued"
    """
    golden_dir = Path(cfg.artifact_root) / cfg.golden_subdir
    golden_dir.mkdir(parents=True, exist_ok=True)

    # Handle conflict_review: always queue for human review regardless of corroboration
    if verifier_verdict == "conflict_review":
        _append_review_queue(
            golden_dir=golden_dir,
            trade_date=trade_date,
            model_label=label,
            verifier_reasons=verifier_reasons,
        )
        log.info(json.dumps({
            "event": "review_queued",
            "trade_date": trade_date,
            "label": label,
        }))
        return "review_queued"

    # Only auto-seed on accept
    if verifier_verdict != "accept":
        return "not_corroborated"

    corroborated, rule, evidence = _check_corroboration(
        label=label,
        digest=digest,
        sources_summary=sources_summary,
        cfg=cfg,
    )

    if not corroborated:
        log.info(json.dumps({
            "event": "not_corroborated",
            "trade_date": trade_date,
            "label": label,
        }))
        return "not_corroborated"

    # Corroborated + accept → attempt to write golden file
    golden_path = golden_dir / f"{trade_date}.json"

    if golden_path.exists():
        try:
            existing = json.loads(golden_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        existing_source = existing.get("source", "human")
        if existing_source != "auto_corroborated":
            # Human file is senior; skip and log
            log.info(json.dumps({
                "event": "skipped_human_senior",
                "trade_date": trade_date,
                "existing_source": existing_source,
            }))
            return "skipped_human_senior"

    golden_record = {
        "date": trade_date,
        "label": label,
        "source": "auto_corroborated",
        "evidence": evidence,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }
    golden_path.write_text(
        json.dumps(golden_record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info(json.dumps({
        "event": "golden_seeded",
        "trade_date": trade_date,
        "label": label,
        "rule": rule,
    }))
    return "seeded"


def _append_review_queue(
    *,
    golden_dir: Path,
    trade_date: str,
    model_label: str,
    verifier_reasons: List[str],
) -> None:
    """Append one entry to golden/review_queue.jsonl."""
    queue_path = golden_dir / "review_queue.jsonl"
    entry = {
        "date": trade_date,
        "model_label": model_label,
        "verifier_reasons": verifier_reasons,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with open(queue_path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


# ---------------------------------------------------------------------------
# Main eval runner
# ---------------------------------------------------------------------------

_AGREEMENT_GATE = 0.90
_CONFLICT_RATE_GATE = 0.10


def run_eval(golden_dir: Path, cfg: SlowTierConfig) -> Dict[str, Any]:
    """Run the eval harness and write runtime/validation/slow_tier_eval.json.

    Returns the eval result dict (same as what is written to disk).
    The result includes a composition breakdown: {n_human, n_auto} to track
    how many golden entries came from human labels vs auto-corroboration.
    """
    golden_entries = load_golden_set(golden_dir)
    n_golden = len(golden_entries)

    labels_path = Path(cfg.artifact_root) / "session_labels.jsonl"
    stored_labels = _load_session_labels(labels_path)

    per_date: List[Dict[str, Any]] = []
    n_agree = 0
    n_conflict = 0
    n_human = 0
    n_auto = 0

    # Classify each golden entry by source
    for entry in golden_entries:
        date = entry["date"]
        golden_path = golden_dir / f"{date}.json"
        try:
            raw = json.loads(golden_path.read_text(encoding="utf-8"))
            source = raw.get("source", "human")
        except (OSError, json.JSONDecodeError):
            source = "human"
        if source == "auto_corroborated":
            n_auto += 1
        else:
            n_human += 1

    for entry in golden_entries:
        result = _eval_one_date(entry, stored_labels)
        per_date.append(result)
        if result["agree"]:
            n_agree += 1
        if result.get("verifier_verdict") == "conflict_review":
            n_conflict += 1

    agreement = n_agree / n_golden if n_golden > 0 else 0.0
    conflict_rate = n_conflict / n_golden if n_golden > 0 else 0.0
    gate_pass = (agreement >= _AGREEMENT_GATE) and (conflict_rate < _CONFLICT_RATE_GATE)

    eval_result: Dict[str, Any] = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "model": cfg.model,
        "n_golden": n_golden,
        "n_human": n_human,
        "n_auto": n_auto,
        "agreement": agreement,
        "conflict_rate": conflict_rate,
        "gate_pass": gate_pass,
        "per_date": per_date,
    }

    # Write artifact
    out_path = Path("runtime") / "validation" / "slow_tier_eval.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(eval_result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return eval_result
