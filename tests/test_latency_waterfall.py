"""CORRECTNESS.md §2 row 10 — waterfall timestamps real.

Groups:
  1. Source-level regression lock  (test_no_shadow_probe_symbol, test_no_shadow_synthetic_emit)
  2. Behavioral                    (test_real_monos_roundtrip, test_waterfall_fields_none, test_no_synthetic_offsets)
  3. Artifact-level                (test_latency_summary_artifact_gate)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _packages_on_path() -> None:
    pkg = str(_REPO / "packages")
    if pkg not in sys.path:
        sys.path.insert(0, pkg)


def _make_cfg(tmp_path: Path) -> Any:
    _packages_on_path()
    from data_system.rithmic_trial.config import TrialConfig

    raw_root = tmp_path / "trial" / "raw"
    normalized_root = tmp_path / "trial" / "normalized"
    replay_root = tmp_path / "trial" / "replay"
    reports_root = tmp_path / "trial" / "reports"
    for d in (raw_root, normalized_root, replay_root, reports_root):
        d.mkdir(parents=True, exist_ok=True)

    return TrialConfig(
        enabled=True,
        connector="fixture",
        symbol="MES",
        exchange="CME",
        contract="MES",
        capture_environment="rithmic_test",
        source="rithmic_trial",
        schema_version="normalized_v1",
        repo_root=tmp_path,
        raw_root=raw_root,
        normalized_root=normalized_root,
        replay_root=replay_root,
        reports_root=reports_root,
    )


def _make_daemon(tmp_path: Path) -> Any:
    _packages_on_path()
    from data_system.rithmic_trial.latency.paper_latency_daemon import PaperLatencyDaemon

    cfg = _make_cfg(tmp_path)
    return PaperLatencyDaemon(cfg, run_id="test-run-001")


# ---------------------------------------------------------------------------
# Group 1 — Source-level regression lock
# ---------------------------------------------------------------------------

_DAEMON_SRC = (
    _REPO
    / "packages"
    / "data_system"
    / "rithmic_trial"
    / "latency"
    / "paper_latency_daemon.py"
)


def test_no_shadow_probe_symbol() -> None:
    """_shadow_probe_mono_ns must not exist in the daemon source."""
    text = _DAEMON_SRC.read_text(encoding="utf-8")
    assert "_shadow_probe_mono_ns" not in text, (
        "paper_latency_daemon still contains _shadow_probe_mono_ns; "
        "synthetic waterfall not removed (CORRECTNESS.md §2 row 10 / defect b)"
    )


def test_no_shadow_synthetic_emit() -> None:
    """No assignment of shadow_synthetic: True must appear in the daemon source."""
    text = _DAEMON_SRC.read_text(encoding="utf-8")
    assert not re.search(r"shadow_synthetic.*True", text), (
        "paper_latency_daemon still emits shadow_synthetic=True; "
        "synthetic waterfall not removed (CORRECTNESS.md §2 row 10 / defect b)"
    )


# ---------------------------------------------------------------------------
# Group 2 — Behavioral
# ---------------------------------------------------------------------------

_TICK_MONO = 1_000_000_000_000  # 1 s in ns — arbitrary known value
_SUBMIT_MONO = 1_000_001_000_000
_ACK_MONO = 1_000_001_500_000


def _market_event() -> dict[str, Any]:
    return {
        "event_type": "trade",
        "symbol": "MES",
        "local_monotonic_receive_ns": _TICK_MONO,
    }


def _submit_event(order_id: str = "ORD-001") -> dict[str, Any]:
    return {
        "event_type": "order_submit",
        "order_id": order_id,
        "symbol": "MES",
        "order_type": "limit",
        "local_monotonic_receive_ns": _SUBMIT_MONO,
    }


def _ack_event(order_id: str = "ORD-001") -> dict[str, Any]:
    return {
        "event_type": "order_ack",
        "order_id": order_id,
        "symbol": "MES",
        "local_monotonic_receive_ns": _ACK_MONO,
    }


def _drive_daemon(tmp_path: Path) -> list[dict[str, Any]]:
    """Drive the daemon through one market + submit + ack cycle; return NDJSON records."""
    daemon = _make_daemon(tmp_path)
    manifest: dict[str, Any] = {}
    daemon._handle_market_event(_market_event(), manifest)
    daemon._handle_order_event(_submit_event(), manifest)
    daemon._handle_order_event(_ack_event(), manifest)
    records = []
    if daemon.records_path.is_file():
        for line in daemon.records_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def test_real_monos_roundtrip(tmp_path: Path) -> None:
    """submit and ack monos must equal the injected values verbatim."""
    records = _drive_daemon(tmp_path)
    assert len(records) >= 1, "no records written after submit+ack pair"
    rec = records[-1]
    assert rec["rithmic_submit_mono_ns"] == _SUBMIT_MONO, (
        f"rithmic_submit_mono_ns mismatch: got {rec.get('rithmic_submit_mono_ns')}, "
        f"expected {_SUBMIT_MONO}"
    )
    assert rec["rithmic_ack_mono_ns"] == _ACK_MONO, (
        f"rithmic_ack_mono_ns mismatch: got {rec.get('rithmic_ack_mono_ns')}, "
        f"expected {_ACK_MONO}"
    )


def test_waterfall_fields_none(tmp_path: Path) -> None:
    """feature/decision/construct/risk waterfall fields must be null in NDJSON record."""
    records = _drive_daemon(tmp_path)
    assert len(records) >= 1, "no records written after submit+ack pair"
    rec = records[-1]
    assert isinstance(rec.get("rithmic_submit_mono_ns"), int), (
        f"rithmic_submit_mono_ns missing or not int; got {rec.get('rithmic_submit_mono_ns')!r}"
    )
    assert rec["rithmic_submit_mono_ns"] == _SUBMIT_MONO, (
        f"rithmic_submit_mono_ns={rec['rithmic_submit_mono_ns']} != expected {_SUBMIT_MONO}"
    )
    assert isinstance(rec.get("rithmic_ack_mono_ns"), int), (
        f"rithmic_ack_mono_ns missing or not int; got {rec.get('rithmic_ack_mono_ns')!r}"
    )
    assert rec["rithmic_ack_mono_ns"] == _ACK_MONO, (
        f"rithmic_ack_mono_ns={rec['rithmic_ack_mono_ns']} != expected {_ACK_MONO}"
    )
    for field_name in (
        "feature_done_mono_ns",
        "decision_start_mono_ns",
        "decision_end_mono_ns",
        "order_construct_mono_ns",
        "risk_check_mono_ns",
    ):
        val = rec.get(field_name)
        assert val is None, (
            f"{field_name} must be null in post-fix daemon; got {val!r}. "
            "Synthetic waterfall offsets still present (defect b)."
        )


def test_no_synthetic_offsets(tmp_path: Path) -> None:
    """No field in the record may equal tick_mono+1000, +1500, or +2000 (synthetic ladder)."""
    records = _drive_daemon(tmp_path)
    assert len(records) >= 1, "no records written after submit+ack pair"
    rec = records[-1]
    tick = rec.get("tick_receive_mono_ns")
    if tick is None:
        return  # tick not stored; no ladder possible
    forbidden = {tick + 1000, tick + 1500, tick + 2000}
    for k, v in rec.items():
        if isinstance(v, int) and v in forbidden:
            pytest.fail(
                f"Field {k}={v} equals tick_receive_mono_ns+offset — "
                "synthetic ladder (t0+1000/+500/+500) still present (defect b)."
            )

    # Also confirm shadow_synthetic key is absent entirely
    assert "shadow_synthetic" not in rec, (
        "shadow_synthetic key present in NDJSON record; "
        "must not be emitted by fixed daemon."
    )


# ---------------------------------------------------------------------------
# Group 3 — Artifact-level
# ---------------------------------------------------------------------------

_SUMMARY_PATH = _REPO / "runtime" / "latency_reports" / "latency_summary.json"


def test_latency_summary_artifact_gate() -> None:
    """If latency_summary.json present and paper_order_latency.measured=true:
    paired_count >= 1000, measurement_tier != 'log_bridge', source not rtrader tier.
    Absent file → skip (CHI404/post-sync gate only).
    """
    if not _SUMMARY_PATH.is_file():
        pytest.skip(
            "latency_summary.json not present; "
            "artifact gate runs on CHI404/after sync"
        )

    data = json.loads(_SUMMARY_PATH.read_text(encoding="utf-8"))
    pol = data.get("paper_order_latency")
    if pol is None:
        pytest.skip("paper_order_latency section absent in latency_summary.json")

    if not pol.get("measured", False):
        pytest.skip("paper_order_latency.measured is false; artifact gate not applicable")

    # Reject synthetic-populated summaries
    assert pol.get("shadow_synthetic") is not True, (
        "paper_order_latency.shadow_synthetic=true in latency_summary.json; "
        "measured=true but sourced from synthetic offsets (defect b)."
    )

    paired = pol.get("paired_count", 0)
    assert paired >= 1000, (
        f"paper_order_latency.paired_count={paired} < 1000; "
        "insufficient real paired measurements."
    )

    tier = pol.get("measurement_tier", "")
    assert tier != "log_bridge", (
        "paper_order_latency.measurement_tier='log_bridge'; "
        "must be direct monotonic capture, not log-bridge tier."
    )

    source = str(pol.get("source", ""))
    assert "rtrader" not in source.lower(), (
        f"paper_order_latency.source={source!r} references rtrader tier; "
        "authoritative summary must use direct monotonic path."
    )
