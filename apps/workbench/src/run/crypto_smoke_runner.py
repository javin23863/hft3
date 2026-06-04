"""Observable crypto candidate smoke loop for the Workbench UI."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def crypto_smoke_root(repo: Path) -> Path:
    return repo / "runtime" / "workbench" / "crypto_smoke"


def latest_status_path(repo: Path) -> Path:
    return crypto_smoke_root(repo) / "latest_status.json"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    for attempt in range(8):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))


def _write_status(run_dir: Path, latest: Path, status: dict[str, Any]) -> None:
    status["updated_at"] = _utc_now()
    _write_json(run_dir / "status.json", status)
    _write_json(latest, status)


def _primary_metrics(report: dict[str, Any]) -> dict[str, Any]:
    primary = (report.get("runs") or {}).get("with_btc_node") or (report.get("runs") or {}).get("without_btc_node") or {}
    return {
        "oos_ic": float(primary.get("oos_ic_baseline_mean", 0.0)),
        "n_rows": int(primary.get("n_rows", 0) or 0),
        "n_folds": int(primary.get("n_folds", 0) or 0),
        "purged_splits": int(primary.get("n_splits", 0) or 0),
    }


def _candidate_summary(report: dict[str, Any]) -> dict[str, Any]:
    primary = _primary_metrics(report)
    proxy_summary = ((report.get("research_pnl_proxy") or {}).get("summary") or {})
    return {
        "candidate_id": report.get("candidate_id", ""),
        "hypothesis_id": report.get("hypothesis_id", ""),
        "target": report.get("target", ""),
        "status": "done",
        "pass_fail": report.get("pass_fail", "unknown"),
        "rejection_reason": report.get("rejection_reason"),
        "holdout_status": (report.get("holdout_gate") or {}).get("status", ""),
        "negative_controls_ok": all(
            bool((report.get("negative_controls") or {}).get(key))
            for key in ("shuffled_degraded", "shifted_degraded")
        ),
        "oos_ic": primary["oos_ic"],
        "n_rows": primary["n_rows"],
        "n_folds": primary["n_folds"],
        "purged_splits": primary["purged_splits"],
        "deflated_sharpe_cdf": report.get("deflated_sharpe_ratio"),
        "proxy_net_pnl_bps": proxy_summary.get("net_pnl_bps"),
        "proxy_num_trades": proxy_summary.get("num_trades"),
        "proxy_profit_factor": proxy_summary.get("profit_factor"),
        "proxy_max_drawdown_bps": proxy_summary.get("max_drawdown_bps"),
        "proxy_sharpe": proxy_summary.get("sharpe_proxy"),
        "proxy_hit_rate": proxy_summary.get("hit_rate"),
        "bh_rejected": bool(report.get("bh_rejected")),
        "execution_ack_scope": report.get("execution_ack_scope", ""),
        "execution_ack_measured": bool(report.get("execution_ack_measured")),
        "order_ack_status": report.get("execution_ack_status", ""),
        "btc_node_evidence_scope": report.get("btc_node_evidence_scope", ""),
        "output_report_path": report.get("output_report_path", ""),
    }


def _rank_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    passed = [c for c in candidates if str(c.get("pass_fail", "")).lower() == "pass"]
    return sorted(
        passed,
        key=lambda c: (
            float(c.get("deflated_sharpe_cdf") or 0.0),
            abs(float(c.get("oos_ic") or 0.0)),
            int(c.get("n_rows") or 0),
        ),
        reverse=True,
    )


def _decision(candidates: list[dict[str, Any]], edge_packets: dict[str, Any] | None = None) -> dict[str, Any]:
    edge_packets = edge_packets or {}
    ranked = _rank_candidates(candidates)
    if not ranked:
        return {
            "action": "QUARANTINE",
            "reason": "no candidate passed smoke evidence gates",
            "top_research_candidate": "",
            "live_registry_ready": False,
            "bitcoin_edge_packet_status": edge_packets.get("status", ""),
        }
    top = ranked[0]
    order_ack = str(top.get("order_ack_status") or "")
    if not top.get("execution_ack_measured") or "INSUFFICIENT" in order_ack.upper():
        return {
            "action": "QUARANTINE",
            "reason": "crypto venue submit-to-ack evidence is insufficient",
            "top_research_candidate": top.get("candidate_id", ""),
            "live_registry_ready": False,
            "bitcoin_edge_packet_status": edge_packets.get("status", ""),
            "blocking_gates": [
                {"gate": "crypto_venue_submit_ack", "status": "INSUFFICIENT"},
                {
                    "gate": "bitcoin_edge_packets",
                    "status": edge_packets.get("status"),
                    "reason": edge_packets.get("reason"),
                },
            ],
        }
    if not edge_packets.get("observed"):
        return {
            "action": "QUARANTINE",
            "reason": "Bitcoin edge packet current-state stream is not observed",
            "top_research_candidate": top.get("candidate_id", ""),
            "live_registry_ready": False,
            "bitcoin_edge_packet_status": edge_packets.get("status", ""),
            "blocking_gates": [
                {
                    "gate": "bitcoin_edge_packets",
                    "status": edge_packets.get("status"),
                    "reason": edge_packets.get("reason"),
                },
            ],
        }
    return {
        "action": "RESEARCH_PASS",
        "reason": "smoke evidence passed; execution evidence still requires operator review",
        "top_research_candidate": top.get("candidate_id", ""),
        "live_registry_ready": False,
        "bitcoin_edge_packet_status": edge_packets.get("status", ""),
    }


def run_crypto_smoke(repo: Path, *, candidate_id: str | None = None) -> dict[str, Any]:
    """Run discovered crypto smoke candidates while writing observable status."""
    from crypto_lane.src.ingest.edge_status import load_edge_packet_status
    from crypto_lane.src.ml.candidate_registry import discover_candidates
    from crypto_lane.src.ml.walk_forward_runner import run_smoke

    started_at = _utc_now()
    edge_packets = load_edge_packet_status(repo)
    run_id = "crypto_smoke_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = crypto_smoke_root(repo) / run_id
    latest = latest_status_path(repo)
    status: dict[str, Any] = {
        "run_id": run_id,
        "scenario": "crypto",
        "state": "running",
        "started_at": started_at,
        "updated_at": started_at,
        "current_stage": "discover_candidates",
        "candidate_filter": candidate_id or "",
        "stages": [
            {"name": "discover_candidates", "status": "running", "started_at": started_at},
            {"name": "walk_forward_smokes", "status": "pending"},
            {"name": "rank_candidates", "status": "pending"},
            {"name": "decision_gate", "status": "pending"},
        ],
        "candidates": [],
        "decision": {},
        "bitcoin_edge_packets": edge_packets,
    }
    _write_status(run_dir, latest, status)

    try:
        candidates = discover_candidates()
        if candidate_id:
            candidates = [c for c in candidates if c.get("candidate_id") == candidate_id]
        status["total_candidates"] = len(candidates)
        status["stages"][0]["status"] = "done"
        status["stages"][0]["finished_at"] = _utc_now()

        if not candidates:
            status["state"] = "blocked"
            status["current_stage"] = "decision_gate"
            status["decision"] = {
                "action": "QUARANTINE",
                "reason": "no crypto candidates discovered from tracked crypto registry",
                "top_research_candidate": "",
                "live_registry_ready": False,
            }
            status["stages"][1]["status"] = "blocked"
            status["stages"][2]["status"] = "blocked"
            status["stages"][3]["status"] = "blocked"
            _write_status(run_dir, latest, status)
            _write_json(run_dir / "summary.json", status)
            return status

        status["current_stage"] = "walk_forward_smokes"
        status["stages"][1]["status"] = "running"
        status["stages"][1]["started_at"] = _utc_now()
        _write_status(run_dir, latest, status)

        completed: list[dict[str, Any]] = []
        for idx, candidate in enumerate(candidates, start=1):
            cid = str(candidate.get("candidate_id", ""))
            entry = {
                "candidate_id": cid,
                "hypothesis_id": candidate.get("hypothesis_id", ""),
                "status": "running",
                "started_at": _utc_now(),
            }
            status["candidates"].append(entry)
            status["active_candidate"] = cid
            status["completed_candidates"] = idx - 1
            _write_status(run_dir, latest, status)
            try:
                report = run_smoke(cid)
                summary = _candidate_summary(report)
                summary["finished_at"] = _utc_now()
                status["candidates"][-1] = summary
                completed.append(summary)
            except Exception as exc:  # pragma: no cover - exercised by operator runs
                status["candidates"][-1] = {
                    **entry,
                    "status": "failed",
                    "pass_fail": "fail",
                    "rejection_reason": str(exc),
                    "finished_at": _utc_now(),
                }
            status["completed_candidates"] = idx
            _write_status(run_dir, latest, status)

        status["active_candidate"] = ""
        status["stages"][1]["status"] = "done"
        status["stages"][1]["finished_at"] = _utc_now()
        status["current_stage"] = "rank_candidates"
        status["stages"][2]["status"] = "running"
        status["stages"][2]["started_at"] = _utc_now()
        status["ranking"] = _rank_candidates(status["candidates"])
        status["stages"][2]["status"] = "done"
        status["stages"][2]["finished_at"] = _utc_now()

        status["current_stage"] = "decision_gate"
        status["stages"][3]["status"] = "running"
        status["stages"][3]["started_at"] = _utc_now()
        edge_packets = load_edge_packet_status(repo)
        status["bitcoin_edge_packets"] = edge_packets
        status["decision"] = _decision(status["candidates"], edge_packets)
        status["stages"][3]["status"] = "done"
        status["stages"][3]["finished_at"] = _utc_now()
        status["state"] = "completed"
        status["finished_at"] = _utc_now()
        _write_status(run_dir, latest, status)
        _write_json(run_dir / "summary.json", status)
        return status
    except Exception as exc:
        status["state"] = "failed"
        status["error"] = str(exc)
        status["finished_at"] = _utc_now()
        _write_status(run_dir, latest, status)
        _write_json(run_dir / "summary.json", status)
        raise
