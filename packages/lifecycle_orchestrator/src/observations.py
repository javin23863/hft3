"""Lifecycle observation producer — evidence artifacts → run_lifecycle_eval input.

Reads out-of-sample / revalidation evidence only (VectorBT paid-screen, HftBacktest
replay, session reports). Missing or stale evidence fails closed — observations never
carry scaffolded GREEN pass labels.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from model_metrics import lifecycle

_DEFAULT_MAX_AGE_DAYS = 90.0
_LINK_KEYS_VBT = ("vectorbt", "vectorbt_results", "vectorbt_artifact", "paid_screen")
_LINK_KEYS_HBT = ("hftbacktest", "hbt", "hbt_observation", "replay_observation")
_LINK_KEYS_SESSION = ("session_report", "session")


@dataclass(frozen=True)
class EvidenceRef:
    kind: str
    path: Path
    observed_at: str
    raw: dict[str, Any]


def _repo_root() -> Path:
    return lifecycle._repo_root()


def _rel_path(path: Path, repo: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _parse_ts(raw: Any) -> Optional[datetime]:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _mtime_iso(path: Path) -> Optional[str]:
    try:
        ts = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _is_stale(observed_at: str, *, max_age_days: float) -> bool:
    dt = _parse_ts(observed_at)
    if dt is None:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - dt > timedelta(days=max_age_days)


def _safe_under_repo(path: Path, repo: Path) -> Optional[Path]:
    try:
        resolved = path.resolve()
        root = repo.resolve()
    except OSError:
        return None
    if root not in resolved.parents and resolved != root:
        return None
    return resolved


def _load_json(path: Path) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _resolve_link(links: dict, keys: tuple[str, ...], repo: Path) -> Optional[Path]:
    for key in keys:
        raw = links.get(key)
        if not raw or not isinstance(raw, str):
            continue
        p = Path(raw)
        if not p.is_absolute():
            p = repo / raw
        safe = _safe_under_repo(p, repo)
        if safe is not None and safe.is_file():
            return safe
    return None


def _pick_evidence(links: dict, repo: Path) -> Optional[EvidenceRef]:
    """Prefer HBT replay observation, then VectorBT OOS, then session report."""
    for kind, keys in (
        ("hbt", _LINK_KEYS_HBT),
        ("vectorbt", _LINK_KEYS_VBT),
        ("session", _LINK_KEYS_SESSION),
    ):
        path = _resolve_link(links, keys, repo)
        if path is None:
            continue
        raw = _load_json(path)
        if raw is None:
            continue
        observed_at = (
            str(raw.get("generated_at") or raw.get("created_at") or raw.get("timestamp") or "")
            or (_mtime_iso(path) or "")
        )
        return EvidenceRef(kind=kind, path=path, observed_at=observed_at, raw=raw)
    return None


def _blocked_observation(
    model_id: str,
    *,
    reason: str,
    source_status: str,
    envelope_id: str = "",
    rearm_gate_context: Optional[dict] = None,
) -> dict:
    """Force non-GREEN classification via data_freshness / missing evidence."""
    obs: dict[str, Any] = {
        "model_id": model_id,
        "regime_id": "",
        "feature_values": {},
        "data_age_ns": 10**18,
        "_evidence": {
            "status": source_status,
            "reason": reason,
            "blocked": True,
        },
    }
    if envelope_id:
        obs["envelope_id"] = envelope_id
    if rearm_gate_context is not None:
        obs["rearm_gate_context"] = rearm_gate_context
    return obs


def _metrics_from_vectorbt(raw: dict) -> dict[str, Any]:
    results = raw.get("vectorbt_results") if isinstance(raw.get("vectorbt_results"), dict) else raw
    if not isinstance(results, dict):
        results = raw
    out: dict[str, Any] = {}
    if (v := results.get("max_drawdown_pct")) is not None:
        try:
            out["drawdown"] = float(v)
        except (TypeError, ValueError):
            pass
    if (v := results.get("win_rate")) is not None:
        try:
            out["win_rate"] = float(v)
        except (TypeError, ValueError):
            pass
    if (v := results.get("oos_expectancy")) is not None:
        try:
            out["expectancy"] = float(v)
        except (TypeError, ValueError):
            pass
    if (v := results.get("slippage_sensitivity")) is not None:
        try:
            out["slippage_bps"] = float(v) * 100.0
        except (TypeError, ValueError):
            pass
    if (v := results.get("num_trades")) is not None:
        try:
            out["trade_count"] = int(v)
        except (TypeError, ValueError):
            pass
    return out


def _metrics_from_hbt(raw: dict) -> dict[str, Any]:
    metrics = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}
    order_state = raw.get("order_state") if isinstance(raw.get("order_state"), dict) else {}
    out: dict[str, Any] = {}
    if (v := metrics.get("fill_rate")) is not None:
        try:
            out["fill_rate"] = float(v)
        except (TypeError, ValueError):
            pass
    if (v := metrics.get("total_slippage")) is not None:
        try:
            out["slippage_bps"] = float(v)
        except (TypeError, ValueError):
            pass
    if (v := metrics.get("latency_p99_ms")) is not None:
        try:
            out["send_to_ack_us"] = float(v) * 1000.0
        except (TypeError, ValueError):
            pass
    if (v := order_state.get("orders_intended")) is not None:
        try:
            out["trade_count"] = int(v)
        except (TypeError, ValueError):
            pass
    submitted = order_state.get("orders_submitted")
    rejected = order_state.get("orders_rejected")
    if submitted is not None and rejected is not None:
        try:
            s, r = float(submitted), float(rejected)
            if s > 0:
                out["order_reject_rate"] = r / s
        except (TypeError, ValueError):
            pass
    return out


def _metrics_from_session(raw: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, target in (
        ("drawdown", "drawdown"),
        ("slippage_bps", "slippage_bps"),
        ("fill_rate", "fill_rate"),
        ("trade_count", "trade_count"),
        ("regime_id", "regime_id"),
    ):
        if key in raw:
            out[target] = raw[key]
    pnl = raw.get("pnl") or raw.get("session_pnl")
    if pnl is not None:
        try:
            out["pnl"] = float(pnl)
        except (TypeError, ValueError):
            pass
    return out


def _apply_envelope_defaults(obs: dict, record: Any, repo: Path) -> None:
    """No synthetic regime/feature injection — observation must come from artifacts."""
    return


def build_observation_for_model(
    model_id: str,
    record: Any,
    *,
    repo_root: Optional[Path] = None,
    max_evidence_age_days: float = _DEFAULT_MAX_AGE_DAYS,
    rearm_gate_context: Optional[dict] = None,
) -> dict:
    """Build one observation dict. Missing/stale evidence returns a blocking observation."""
    repo = repo_root or _repo_root()
    links = (
        getattr(record, "research_card_links", None)
        or (record.get("research_card_links") if isinstance(record, dict) else None)
        or {}
    )
    if not isinstance(links, dict):
        links = {}
    eid = getattr(record, "current_envelope_id", None) or (
        record.get("current_envelope_id") if isinstance(record, dict) else ""
    )

    evidence = _pick_evidence(links, repo)
    if evidence is None:
        return _blocked_observation(
            model_id,
            reason="no revalidation artifact linked",
            source_status="missing",
            envelope_id=str(eid or ""),
            rearm_gate_context=rearm_gate_context,
        )

    if _is_stale(evidence.observed_at, max_age_days=max_evidence_age_days):
        return _blocked_observation(
            model_id,
            reason=f"stale evidence ({evidence.kind})",
            source_status="stale",
            envelope_id=str(eid or ""),
            rearm_gate_context=rearm_gate_context,
        )

    if evidence.kind == "vectorbt":
        metrics = _metrics_from_vectorbt(evidence.raw)
    elif evidence.kind == "hbt":
        metrics = _metrics_from_hbt(evidence.raw)
    else:
        metrics = _metrics_from_session(evidence.raw)

    if not metrics:
        return _blocked_observation(
            model_id,
            reason=f"empty metrics in {evidence.kind} artifact",
            source_status="missing",
            envelope_id=str(eid or ""),
            rearm_gate_context=rearm_gate_context,
        )

    obs: dict[str, Any] = {
        "model_id": model_id,
        "regime_id": metrics.pop("regime_id", "") or "",
        "feature_values": {},
        **metrics,
        "_evidence": {
            "status": "ok",
            "kind": evidence.kind,
            "source_path": _rel_path(evidence.path, repo),
            "observed_at": evidence.observed_at,
            "blocked": False,
        },
    }
    _apply_envelope_defaults(obs, record, repo)
    if rearm_gate_context is not None:
        obs["rearm_gate_context"] = rearm_gate_context
    return obs


def build_observations(
    *,
    model_ids: Optional[list[str]] = None,
    only_states: Optional[frozenset[str]] = None,
    repo_root: Optional[Path] = None,
    max_evidence_age_days: float = _DEFAULT_MAX_AGE_DAYS,
    rearm_gate_context_by_model: Optional[dict[str, dict]] = None,
) -> dict[str, dict]:
    """Build ``{model_id: observation_dict}`` for LIVE/DEGRADED models by default."""
    states = only_states or frozenset({lifecycle.LIVE, lifecycle.DEGRADED})
    reg = lifecycle.load_registry()
    out: dict[str, dict] = {}
    ctx_map = rearm_gate_context_by_model or {}
    for mid, rec in reg.items():
        if model_ids is not None and mid not in model_ids:
            continue
        if rec.current_state not in states:
            continue
        out[mid] = build_observation_for_model(
            mid,
            rec,
            repo_root=repo_root,
            max_evidence_age_days=max_evidence_age_days,
            rearm_gate_context=ctx_map.get(mid),
        )
    return out


def write_observations_file(path: Path, observations: dict[str, dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(observations, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)
    return path
