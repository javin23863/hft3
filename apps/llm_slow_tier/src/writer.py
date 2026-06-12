"""Artifact writer for F1 session labels.

Appends one JSON-line record to session_labels.jsonl.

Durability: the file is opened in append mode, the line is written with a
trailing newline, and the OS file descriptor is flushed + fsynced before
the function returns.  This makes the write as durable as the underlying
filesystem on power loss (no temp-file rename needed for append-only).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .config import SlowTierConfig
from .digest import Digest
from .labeler import LabelResult, PROMPT_TEMPLATE_HASH
from .verify import VerifierResult


def append_session_label(
    *,
    trade_date: str,
    label_result: LabelResult,
    verifier_result: VerifierResult,
    digest: Digest,
    sources_summary: dict,
    model: str,
    cfg: SlowTierConfig,
) -> Path:
    """Append one labeled-session record to session_labels.jsonl.

    Returns the path of the JSONL file.
    Raises OSError on write failure.
    """
    out_dir = Path(cfg.artifact_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "session_labels.jsonl"

    record = {
        "trade_date": trade_date,
        "label": verifier_result.final_label,
        "drivers": label_result.drivers,
        "confidence": label_result.confidence,
        "verifier_verdict": verifier_result.verdict,
        "verifier_reasons": verifier_result.reasons,
        "model": model,
        "prompt_template_hash": PROMPT_TEMPLATE_HASH,
        "digest": digest.to_dict(),
        "sources_summary": sources_summary,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "advisory": True,  # Labels stay advisory until the eval gate passes (§7)
    }

    line = json.dumps(record, ensure_ascii=False) + "\n"

    with open(out_path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())

    return out_path
