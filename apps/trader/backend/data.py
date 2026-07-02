"""Trusted data loaders for the trader dashboard.

Every payload this module returns is evidence-wrapped: values arrive with
the receipt path, the receipt sha256, and a freshness age. Anything that
cannot be verified renders as BLOCKED upstream — never as a number.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
RUN_INDEX_PATH = REPO / "runtime" / "run_index" / "hbt_run_index.jsonl"
LIFECYCLE_REGISTRY = REPO / "runtime" / "lifecycle" / "model_lifecycle.json"
LIFECYCLE_TRANSITIONS = REPO / "runtime" / "lifecycle" / "transitions.jsonl"
CAMPAIGN_MONITOR_GLOBS = (
    REPO / "runtime" / "vast_receipts",
    REPO / "runtime" / "reports",
)


@dataclass
class Evidence:
    """Provenance wrapper: where a payload came from and whether to trust it."""

    path: str
    sha256: str = ""
    age_seconds: float | None = None
    status: str = "ok"  # ok | missing | corrupt | stale
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "age_seconds": self.age_seconds,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass
class LoadedDocument:
    payload: Any
    evidence: Evidence
    rows: list[dict[str, Any]] = field(default_factory=list)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _age_seconds(path: Path) -> float:
    return max(0.0, time.time() - path.stat().st_mtime)


def load_json_document(path: Path) -> LoadedDocument:
    if not path.is_file():
        return LoadedDocument(
            payload=None,
            evidence=Evidence(path=str(path), status="missing", reason="file_not_found"),
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        sha256 = _sha256_file(path)
        age_seconds = _age_seconds(path)
    except (OSError, json.JSONDecodeError) as exc:
        # OSError can also fire on the hash/stat re-open (permission race
        # after is_file()); either way the document is unverifiable.
        return LoadedDocument(
            payload=None,
            evidence=Evidence(
                path=str(path), status="corrupt", reason=f"{type(exc).__name__}"
            ),
        )
    return LoadedDocument(
        payload=payload,
        evidence=Evidence(path=str(path), sha256=sha256, age_seconds=age_seconds),
    )


def load_run_index(path: Path | None = None) -> LoadedDocument:
    """Load the run index JSONL and verify its embedded self-check hash."""
    if path is None:
        path = RUN_INDEX_PATH  # module attr read at call time (testable)
    if not path.is_file():
        return LoadedDocument(
            payload=None,
            evidence=Evidence(
                path=str(path),
                status="missing",
                reason="run index not built — run scripts/build_run_index.py",
            ),
        )
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        parsed = [json.loads(line) for line in lines if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        return LoadedDocument(
            payload=None,
            evidence=Evidence(path=str(path), status="corrupt", reason=type(exc).__name__),
        )
    if not parsed or parsed[-1].get("row_kind") != "index_summary":
        return LoadedDocument(
            payload=None,
            evidence=Evidence(
                path=str(path), status="corrupt", reason="index_summary_row_missing"
            ),
        )
    summary = parsed[-1]
    rows = parsed[:-1]
    recomputed = hashlib.sha256(
        "\n".join(lines[: len(rows)]).encode("utf-8")
    ).hexdigest()
    if recomputed != summary.get("data_sha256"):
        return LoadedDocument(
            payload=None,
            evidence=Evidence(
                path=str(path),
                status="corrupt",
                reason="index_self_check_hash_mismatch",
            ),
        )
    return LoadedDocument(
        payload=summary,
        rows=rows,
        evidence=Evidence(
            path=str(path), sha256=_sha256_file(path), age_seconds=_age_seconds(path)
        ),
    )


def load_lifecycle() -> tuple[LoadedDocument, LoadedDocument]:
    registry = load_json_document(LIFECYCLE_REGISTRY)
    transitions = LoadedDocument(
        payload=None,
        evidence=Evidence(path=str(LIFECYCLE_TRANSITIONS), status="missing", reason="file_not_found"),
    )
    if LIFECYCLE_TRANSITIONS.is_file():
        rows: list[dict[str, Any]] = []
        status, reason = "ok", ""
        sha256, age_seconds = "", None
        try:
            for line in LIFECYCLE_TRANSITIONS.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
            # Hash/stat inside the try: the re-open can OSError on a
            # permission race after is_file(); that is corrupt, not a 500.
            sha256 = _sha256_file(LIFECYCLE_TRANSITIONS)
            age_seconds = _age_seconds(LIFECYCLE_TRANSITIONS)
        except (OSError, json.JSONDecodeError) as exc:
            status, reason = "corrupt", type(exc).__name__
        transitions = LoadedDocument(
            payload=None,
            rows=rows,
            evidence=Evidence(
                path=str(LIFECYCLE_TRANSITIONS),
                sha256=sha256,
                age_seconds=age_seconds,
                status=status,
                reason=reason,
            ),
        )
    return registry, transitions


def find_campaign_monitor_documents(max_age_days: float = 14.0) -> list[LoadedDocument]:
    """Collect recent campaign monitor/watchdog/teardown JSON receipts."""
    docs: list[LoadedDocument] = []
    cutoff = time.time() - max_age_days * 86400.0
    for root in CAMPAIGN_MONITOR_GLOBS:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.json")):
            name = path.name.lower()
            if not any(k in name for k in ("monitor", "watchdog", "campaign", "teardown", "canary")):
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    continue
            except OSError:
                continue
            docs.append(load_json_document(path))
    return docs
