"""Persist and load backtester certification registry."""
from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_REGISTRY_REL = Path("runtime/validation/certification_registry.json")


@dataclass
class CertificationRecord:
    latest_certification_run_id: str = ""
    latest_certification_commit: str = ""
    latest_certification_timestamp: str = ""
    latest_certification_status: str = "MISSING"
    backtester_version: str = "unknown"
    covered_modules: list[str] = field(default_factory=list)
    covered_symbols: list[str] = field(default_factory=list)
    covered_event_types: list[str] = field(default_factory=list)
    covered_latency_bands: list[float] = field(default_factory=list)
    covered_queue_models: list[str] = field(default_factory=list)
    covered_execution_modes: list[str] = field(default_factory=list)
    scorecard_path: str = ""
    blocking_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CertificationRecord:
        return cls(
            latest_certification_run_id=str(raw.get("latest_certification_run_id", "")),
            latest_certification_commit=str(raw.get("latest_certification_commit", "")),
            latest_certification_timestamp=str(raw.get("latest_certification_timestamp", "")),
            latest_certification_status=str(raw.get("latest_certification_status", "MISSING")),
            backtester_version=str(raw.get("backtester_version", "unknown")),
            covered_modules=list(raw.get("covered_modules", [])),
            covered_symbols=list(raw.get("covered_symbols", [])),
            covered_event_types=list(raw.get("covered_event_types", [])),
            covered_latency_bands=[float(x) for x in raw.get("covered_latency_bands", [])],
            covered_queue_models=list(raw.get("covered_queue_models", [])),
            covered_execution_modes=list(raw.get("covered_execution_modes", [])),
            scorecard_path=str(raw.get("scorecard_path", "")),
            blocking_failures=list(raw.get("blocking_failures", [])),
            warnings=list(raw.get("warnings", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def registry_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / DEFAULT_REGISTRY_REL


def git_sha(root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def backtester_version(root: Path | None = None) -> str:
    root = root or repo_root()
    try:
        out = subprocess.check_output(
            ["git", "describe", "--always", "--dirty"],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        short = git_sha(root)
        return short[:12] if short != "unknown" else "unknown"


def load_registry(root: Path | None = None) -> CertificationRecord:
    path = registry_path(root)
    if not path.is_file():
        return CertificationRecord()
    return CertificationRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_registry(record: CertificationRecord, root: Path | None = None) -> Path:
    path = registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def get_latest_status(root: Path | None = None) -> str:
    return load_registry(root).latest_certification_status


def new_certification_run_id() -> str:
    return f"CERT-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
