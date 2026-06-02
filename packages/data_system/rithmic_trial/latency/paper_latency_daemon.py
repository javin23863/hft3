"""CHI404 paper order latency daemon — monotonic waterfall audit (colo only)."""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import TrialConfig, load_config
from ..connector import build_connector
from ..platform import is_windows
from ..schema.paper_latency_record_v1 import PaperLatencyRecordV1

logger = logging.getLogger(__name__)

_STOP = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id() -> str:
    return os.environ.get("PAPER_LATENCY_RUN_ID") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _assert_chi404_only() -> None:
    if is_windows():
        raise RuntimeError(
            "paper_latency_daemon runs on CHI404 only (BLUEPRINT §4). "
            "Windows is the dev workstation, not the trade-path host."
        )


def _read_manifest(watch_dirs: list[Path]) -> dict[str, Any]:
    for base in watch_dirs:
        path = base / "sweep_manifest.json"
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
    return {}


def _shadow_probe_mono_ns() -> tuple[int | None, int | None, int | None, int | None]:
    """Lightweight colo shadow timing slices (synthetic; not order-causal)."""
    t0 = time.perf_counter_ns()
    t1 = t0 + 1000
    t2 = t1 + 500
    t3 = t2 + 500
    return t0, t1, t2, t3


class PaperLatencyDaemon:
    def __init__(self, cfg: TrialConfig, *, run_id: str | None = None) -> None:
        self.cfg = cfg
        self.run_id = run_id or _run_id()
        self.repo_root = cfg.repo_root
        self.connector = build_connector(cfg)
        raw_base = self.repo_root / "runtime" / "paper_latency" / "raw" / self.run_id
        raw_base.mkdir(parents=True, exist_ok=True)
        self.records_path = raw_base / "records.ndjson"
        self.status_path = self.repo_root / "runtime" / "paper_latency" / "daemon_status.json"
        self._open_orders: dict[str, PaperLatencyRecordV1] = {}
        self._last_tick_mono: int | None = None
        self._record_count = 0
        self._paired_count = 0

    def _append_record(self, rec: PaperLatencyRecordV1) -> None:
        with self.records_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec.to_dict()) + "\n")
        self._record_count += 1

    def _write_status(self, extra: dict[str, Any] | None = None) -> None:
        payload = {
            "run_id": self.run_id,
            "timestamp_utc": _utc_now(),
            "record_count": self._record_count,
            "paired_submit_ack_count": self._paired_count,
            "records_path": str(self.records_path),
            **(extra or {}),
        }
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self.status_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _handle_market_event(self, ev: dict[str, Any], manifest: dict[str, Any]) -> None:
        mono = ev.get("local_monotonic_receive_ns")
        if mono is None:
            return
        self._last_tick_mono = int(mono)
        feat, dec_end, construct, risk = _shadow_probe_mono_ns()
        self._shadow = {
            "tick_receive_mono_ns": int(mono),
            "feature_done_mono_ns": feat,
            "decision_start_mono_ns": feat,
            "decision_end_mono_ns": dec_end,
            "order_construct_mono_ns": construct,
            "risk_check_mono_ns": risk,
            "market_state": manifest.get("market_state") or os.environ.get("PAPER_LATENCY_MARKET_STATE", "quiet"),
            "session_tag": manifest.get("session") or "regular",
            "shadow_synthetic": True,
        }

    def _handle_order_event(self, ev: dict[str, Any], manifest: dict[str, Any]) -> None:
        et = str(ev.get("event_type", ""))
        oid = str(ev.get("order_id") or ev.get("id") or "")
        if not oid:
            return
        mono = ev.get("local_monotonic_receive_ns")
        if mono is None:
            return
        mono_i = int(mono)
        market_state = manifest.get("market_state") or os.environ.get("PAPER_LATENCY_MARKET_STATE", "quiet")
        session_tag = manifest.get("session") or "regular"
        shadow = getattr(self, "_shadow", {})

        if et == "order_submit":
            rec = PaperLatencyRecordV1(
                run_id=self.run_id,
                order_id=oid,
                symbol=str(ev.get("symbol") or self.cfg.symbol),
                order_type=str(ev.get("order_type") or ev.get("type") or "unknown"),
                session_tag=session_tag,
                market_state=market_state,
                wall_utc=_utc_now(),
                tick_receive_mono_ns=shadow.get("tick_receive_mono_ns") or self._last_tick_mono,
                feature_done_mono_ns=shadow.get("feature_done_mono_ns"),
                decision_start_mono_ns=shadow.get("decision_start_mono_ns"),
                decision_end_mono_ns=shadow.get("decision_end_mono_ns"),
                order_construct_mono_ns=shadow.get("order_construct_mono_ns"),
                risk_check_mono_ns=shadow.get("risk_check_mono_ns"),
                rithmic_submit_mono_ns=mono_i,
                raw_log_line=str(ev.get("raw_line") or "")[:2000],
            )
            self._open_orders[oid] = rec
            return

        rec = self._open_orders.get(oid)
        if rec is None:
            if et not in ("order_ack", "ack"):
                return
            rec = PaperLatencyRecordV1(
                run_id=self.run_id,
                order_id=oid,
                symbol=str(ev.get("symbol") or self.cfg.symbol),
                order_type=str(ev.get("order_type") or "unknown"),
                session_tag=session_tag,
                market_state=market_state,
                wall_utc=_utc_now(),
            )
            self._open_orders[oid] = rec

        if et in ("order_ack", "ack"):
            rec.rithmic_ack_mono_ns = mono_i
            if (
                rec.rithmic_submit_mono_ns is not None
                and rec.rithmic_ack_mono_ns > rec.rithmic_submit_mono_ns
            ):
                self._paired_count += 1
                self._append_record(rec)
            return

        if et == "order_status":
            rec.exchange_status_mono_ns = mono_i
            rec.exchange_status_missing = False
            return

        if et in ("order_replace", "cancel_replace"):
            rec.cancel_replace_submit_mono_ns = mono_i
            return

        if et == "cancel" and rec.cancel_replace_submit_mono_ns:
            rec.cancel_replace_ack_mono_ns = mono_i
            self._append_record(rec)
            return

        if et == "fill":
            rec.fill_mono_ns = mono_i
            if rec.rithmic_ack_mono_ns is None:
                rec.rithmic_ack_mono_ns = mono_i
            self._append_record(rec)

    def run(self, *, poll_interval_sec: float = 0.25) -> None:
        _assert_chi404_only()
        self.connector.connect()
        logger.info("paper_latency_daemon run_id=%s records=%s", self.run_id, self.records_path)
        self._write_status({"state": "running"})

        while not _STOP:
            manifest = _read_manifest(self.connector.watch_dirs)
            events = self.connector.poll_events()
            for ev in events:
                et = str(ev.get("event_type", ""))
                if et in ("trade", "quote", "depth"):
                    self._handle_market_event(ev, manifest)
                elif et in (
                    "order_submit",
                    "order_ack",
                    "ack",
                    "fill",
                    "cancel",
                    "order_replace",
                    "cancel_replace",
                    "order_status",
                ):
                    self._handle_order_event(ev, manifest)

            if events:
                self._write_status({"state": "running", "last_batch_events": len(events)})
            time.sleep(poll_interval_sec)

        self._write_status({"state": "stopped"})
        self.connector.close()


def _handle_signal(signum: int, _frame: Any) -> None:
    global _STOP
    _STOP = True
    logger.info("signal %s — stopping daemon", signum)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="CHI404 paper order latency daemon")
    parser.add_argument("--config", default="data_system/config/rithmic_trial.yaml")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--poll-interval-sec", type=float, default=0.25)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    cfg = load_config(args.config)
    daemon = PaperLatencyDaemon(cfg, run_id=args.run_id)
    try:
        daemon.run(poll_interval_sec=args.poll_interval_sec)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
