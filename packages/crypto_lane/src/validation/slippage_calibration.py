"""Slippage calibration — fit fill+slippage distribution from Bitfinex L3 MBO replay.

K12/CRYPTO_LIVE.md §6.3 contract: `is_calibration_stale` is the gate surface for
the crypto bundle build (C11). Callers must fail-closed when it returns (True, reason).

Slippage std is population std (ddof=0). The artifact key ``slippage_std_ddof`` is
set to 0 to make this contract machine-readable.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from crypto_lane.src.validation.crypto_validation_workflow import validate_crypto_candidate

DEFAULT_ARTIFACT_REL = Path("runtime/crypto_latency/slippage_calibration.json")
DEFAULT_RAW_DIR_REL = Path("data/crypto/bitfinex_mbo_raw")
MIN_FILLS_ACCEPT = 30
# Envelope narrower than 1e-6 bps is float noise, not market variance — reject as degenerate.
MIN_ENVELOPE_STD_BPS = 1e-6


def default_artifact_path(repo_root: Path) -> Path:
    """Return absolute path to the default slippage calibration artifact."""
    return repo_root / DEFAULT_ARTIFACT_REL


def acceptance_for(num_fills: int, exec_cls: str, slippage_vs_mid_bps_std: float) -> str:
    """Return acceptance string for a single calibration result.

    Pure function — no I/O, no side effects; extracted for unit-testability.
    """
    fills_and_cls_ok = num_fills >= MIN_FILLS_ACCEPT and exec_cls == "L3_VALIDATED"
    if fills_and_cls_ok and slippage_vs_mid_bps_std >= MIN_ENVELOPE_STD_BPS:
        return "OK"
    elif fills_and_cls_ok:
        return "DEGENERATE_ZERO_VARIANCE"
    else:
        return "INSUFFICIENT"


def calibrate_slippage(
    repo_root: Path,
    candidates: List[Any],
    *,
    latency_ms: float = 50.0,
    max_steps: int = 20000,
    signal_period: int = 50,
) -> Dict[str, Any]:
    """Run deterministic replay over *candidates* and return a calibration artifact dict.

    K12/CRYPTO_LIVE.md §6.3 contract: builds an alternating long/short forcing signal
    that guarantees marketable-limit intents through the position guard, then calls
    validate_crypto_candidate for each candidate and records the slippage distribution.
    """
    signal_sequence = [
        1.0 if (i // signal_period) % 2 == 0 else -1.0
        for i in range(max_steps)
    ]

    raw_dir = repo_root / DEFAULT_RAW_DIR_REL
    session_files = sorted(raw_dir.glob("bitfinex_mbo_*.ndjson")) if raw_dir.exists() else []
    sessions = [f.name for f in session_files]
    newest_mtime_epoch = max((f.stat().st_mtime for f in session_files), default=0.0)
    newest_mtime_utc = (
        datetime.fromtimestamp(newest_mtime_epoch, tz=timezone.utc).isoformat()
        if newest_mtime_epoch > 0.0
        else ""
    )

    results: List[Dict[str, Any]] = []
    for candidate in candidates:
        symbol: str = candidate.metadata.get("symbol", "UNKNOWN")
        report = validate_crypto_candidate(
            candidate,
            repo_root,
            latency_ms=latency_ms,
            max_steps=max_steps,
            signal_sequence=signal_sequence,
        )
        r = report.result
        num_fills: int = r.num_fills
        exec_cls: str = report.execution_classification
        acceptance = acceptance_for(num_fills, exec_cls, r.slippage_vs_mid_bps_std)
        results.append(
            {
                "symbol": symbol,
                "execution_classification": exec_cls,
                "fill_rate": r.fill_rate,
                "mean_slippage_bps": r.slippage_bps,
                "slippage_bps_std": r.slippage_bps_std,
                "slippage_bps_p95": r.slippage_bps_p95,
                "slippage_vs_mid_bps": r.slippage_vs_mid_bps,
                "slippage_vs_mid_bps_std": r.slippage_vs_mid_bps_std,
                "slippage_vs_mid_bps_p95": r.slippage_vs_mid_bps_p95,
                "envelope_basis": "slippage_vs_mid_bps",
                "num_fills": num_fills,
                "num_fills_with_mid": r.num_fills_with_mid,
                "num_intents": r.num_intents,
                "queue_model": "L3FifoQueueModel",
                "latency_ms": latency_ms,
                "acceptance": acceptance,
            }
        )

    return {
        "calibration_version": 1,
        "fit_utc": datetime.now(tz=timezone.utc).isoformat(),
        "fit_epoch": time.time(),
        "sessions": sessions,
        "newest_capture_mtime_utc": newest_mtime_utc,
        "newest_capture_mtime_epoch": newest_mtime_epoch,
        "results": results,
        "signal_profile": (
            f"alternating +/-1.0 blocks, period {signal_period}, max_steps {max_steps}"
        ),
        "slippage_std_ddof": 0,
    }


def write_calibration_artifact(artifact: Dict[str, Any], path: Path) -> Path:
    """Persist *artifact* as indented JSON at *path*; create parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def is_calibration_stale(artifact_path: Path, raw_dir: Path) -> Tuple[bool, str]:
    """Return (stale, reason) — the K12/CRYPTO_LIVE.md §6.3 gate surface.

    Callers (C11 crypto bundle build) must fail-closed on True.
    """
    if not artifact_path.exists():
        return (True, "artifact absent")

    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception:
        return (True, "artifact unparseable")

    fit_epoch: float = float(artifact.get("fit_epoch", 0.0))
    if not raw_dir.exists():
        return (True, "no captures found")
    session_files = list(raw_dir.glob("bitfinex_mbo_*.ndjson"))
    if not session_files:
        return (True, "no captures found")
    for ndjson in session_files:
        if ndjson.stat().st_mtime > fit_epoch:
            return (True, f"newer capture exists: {ndjson.name}")

    results: List[Dict[str, Any]] = artifact.get("results", [])
    if not any(r.get("acceptance") == "OK" for r in results):
        return (True, "no acceptance=OK result")

    return (False, "fresh")
