"""Compact AAR/KG review memory for machine ideation packets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _rel(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _candidate_roots(repo_root: Path) -> Iterable[Path]:
    for root in (
        repo_root / "artifacts" / "workbench_runs",
        repo_root / "research_cards" / "workbench_runs",
        repo_root / "runtime" / "workbench" / "crypto_smoke",
    ):
        if root.is_dir():
            yield root


def _fact_codes(response: Dict[str, Any], symbolic: Dict[str, Any], meta: Dict[str, Any]) -> List[str]:
    codes: List[str] = []
    llm_status = str(response.get("llm_status") or meta.get("llm_status") or "missing")
    codes.append(f"llm:{llm_status}")
    symbolic_passed = response.get("symbolic_passed", symbolic.get("passed"))
    codes.append("symbolic:pass" if symbolic_passed is True else "symbolic:fail")
    decision = response.get("decision") or {}
    if decision.get("promote_candidate_recommendation") is True:
        codes.append("llm_promote_recommendation:true")
    elif "promote_candidate_recommendation" in decision:
        codes.append("llm_promote_recommendation:false")
    if meta.get("report_written") is True:
        codes.append("report:written")
    if meta.get("response_written") is True:
        codes.append("response:written")
    return codes[:8]


def _metric_values(packet: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    lat = packet.get("latency_authority") or {}
    for key in ("net_pnl", "breakeven_us"):
        value = lat.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = float(value)
    elapsed = meta.get("llm_elapsed_s")
    if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool):
        out["llm_elapsed_s"] = float(elapsed)
    return out


def _event_matches(packet: Dict[str, Any], event_id: str) -> bool:
    if not event_id:
        return True
    evt = packet.get("event_context") or {}
    packet_event = str(evt.get("event_id") or "")
    return not packet_event or packet_event == event_id


def build_review_memory(
    repo_root: Path,
    *,
    event_id: str,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Return bounded, advisory-only machine facts from prior AAR artifacts."""
    repo_root = Path(repo_root)
    items: List[Dict[str, Any]] = []
    for root in _candidate_roots(repo_root):
        for response_path in sorted(
            root.glob("**/after_action_response.json"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        ):
            run_dir = response_path.parent
            packet = _read_json(run_dir / "after_action_packet.json")
            if not _event_matches(packet, event_id):
                continue
            response = _read_json(response_path)
            symbolic = _read_json(run_dir / "after_action_symbolic.json")
            meta = _read_json(run_dir / "after_action_meta.json")
            memory_id = f"mem_{len(items) + 1:03d}"
            ref_id = f"ref_{memory_id}"
            item = {
                "memory_id": memory_id,
                "ref_id": ref_id,
                "fact_codes": _fact_codes(response, symbolic, meta),
                "metric_values": _metric_values(packet, meta),
                "authority": "advisory",
                "source_ref": _rel(repo_root, response_path),
            }
            items.append(item)
            if len(items) >= limit:
                return items
    return items


def memory_refs(memory: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    refs: Dict[str, Dict[str, str]] = {}
    for item in memory:
        ref_id = str(item.get("ref_id") or "")
        source_ref = str(item.get("source_ref") or "")
        if ref_id and source_ref:
            refs[ref_id] = {"type": "artifact", "value": source_ref}
    return refs


def schema_memory_items(memory: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in memory:
        out.append(
            {
                "memory_id": str(item.get("memory_id") or ""),
                "ref_id": str(item.get("ref_id") or ""),
                "fact_codes": list(item.get("fact_codes") or []),
                "metric_values": dict(item.get("metric_values") or {}),
                "authority": "advisory",
            }
        )
    return out
