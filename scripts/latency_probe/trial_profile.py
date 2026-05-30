"""Read latest Rithmic trial latency_profile.json with trusted/untrusted classification."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def profile_untrusted(latency_data: dict[str, Any], capture: dict[str, Any] | None) -> bool:
    texts: list[str] = []
    lims = latency_data.get("limitations")
    if isinstance(lims, list):
        texts.extend(str(x) for x in lims)
    elif isinstance(lims, str):
        texts.append(lims)

    connector: str | None = None
    if capture:
        manifest = capture.get("manifest") or {}
        known = capture.get("limitations") or manifest.get("known_limitations") or {}
        if isinstance(known, dict):
            connector = known.get("connector")
            note = known.get("note")
            if note:
                texts.append(str(note))

    if connector == "fixture":
        return True
    return any("Synthetic fixture" in t for t in texts)


def latest_latency_profile(repo_root: Path) -> dict[str, Any] | None:
    base = repo_root / "reports" / "rithmic_trial"
    if not base.is_dir():
        return None
    candidates = sorted(
        (p for p in base.iterdir() if p.is_dir() and (p / "latency_profile.json").is_file()),
        key=lambda p: p.name,
        reverse=True,
    )
    if not candidates:
        return None
    profile_dir = candidates[0]
    path = profile_dir / "latency_profile.json"
    data = load_json(path)
    if data is None:
        return None

    capture_path = profile_dir / "data_capture_report.json"
    capture = load_json(capture_path) if capture_path.is_file() else None
    trusted = not profile_untrusted(data, capture)
    connector: str | None = None
    if capture:
        manifest = capture.get("manifest") or {}
        known = capture.get("limitations") or manifest.get("known_limitations") or {}
        if isinstance(known, dict):
            connector = known.get("connector")

    out: dict[str, Any] = {
        "path": str(path.relative_to(repo_root)).replace("\\", "/"),
        "profile_date": profile_dir.name,
        "order_rtt_ms": data.get("order_rtt_ms"),
        "order_submit_to_ack_us": data.get("order_submit_to_ack_us"),
        "feed_latency_us": data.get("feed_latency_us"),
        "status": data.get("status"),
        "limitations": data.get("limitations"),
        "trusted": trusted,
        "connector": connector,
    }
    if capture is not None:
        out["data_capture_report"] = str(capture_path.relative_to(repo_root)).replace("\\", "/")
    return out
