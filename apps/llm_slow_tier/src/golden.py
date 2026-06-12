"""Golden-set loader and eval harness for the LLM slow-tier lane.

Golden files are stored as individual JSON files in:
    artifacts/research_cards/slow_tier/golden/{date}.json

Each file has the schema:
    {"date": "YYYY-MM-DD", "label": "<label>", "notes": "<optional string>"}

The eval runner re-labels each golden date by replaying the labeler against
stored digests (loaded from stored model outputs in session_labels.jsonl, or
by re-running digest+label if no stored output exists).  It then computes:
  - agreement rate: fraction of dates where final_label == golden_label
  - conflict_rate: fraction of dates where verifier_verdict == "conflict_review"

Gate (LLM_SLOW_TIER.md §7):
  gate_pass = (agreement >= 0.90) AND (conflict_rate < 0.10)

Output is written to runtime/validation/slow_tier_eval.json.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import SlowTierConfig


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
# Main eval runner
# ---------------------------------------------------------------------------

_AGREEMENT_GATE = 0.90
_CONFLICT_RATE_GATE = 0.10


def run_eval(golden_dir: Path, cfg: SlowTierConfig) -> Dict[str, Any]:
    """Run the eval harness and write runtime/validation/slow_tier_eval.json.

    Returns the eval result dict (same as what is written to disk).
    """
    golden_entries = load_golden_set(golden_dir)
    n_golden = len(golden_entries)

    labels_path = Path(cfg.artifact_root) / "session_labels.jsonl"
    stored_labels = _load_session_labels(labels_path)

    per_date: List[Dict[str, Any]] = []
    n_agree = 0
    n_conflict = 0

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
