from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .capture.live_capture import LiveCapture
from .config import TrialConfig, load_config
from .connector import build_connector
from .pipeline import cmd_process
from .platform import is_windows


def _utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def _write_status(reports_dir: Path, payload: dict[str, Any]) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / "unattended_status.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _wine_rtrader_running() -> bool:
    try:
        out = subprocess.check_output(
            ["pgrep", "-af", "wine"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        lowered = out.lower()
        return "rtrader" in lowered or "rithmic" in lowered
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def ensure_rtrader_started(cfg: TrialConfig) -> Path | None:
    """Start R|Trader under Wine on CHI404 via launch_rtrader.sh (colo only)."""
    rt = cfg.rtrader
    launch = Path(rt.get("launch_script", "/root/hft3/logs/rtrader/launch_rtrader.sh"))
    if not launch.is_absolute():
        launch = cfg.repo_root / launch

    if rt.get("skip_start_if_running", True) and _wine_rtrader_running():
        logging.info("R|Trader (Wine) already running")
        return launch if launch.exists() else None

    if not launch.exists():
        logging.warning(
            "R|Trader launch script not found: %s — run infrastructure/chi404/08_rtrader_wine_setup.sh",
            launch,
        )
        return None

    logging.info("Starting R|Trader via %s", launch)
    subprocess.Popen(
        ["bash", str(launch)],
        cwd=str(launch.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(float(rt.get("startup_wait_sec", 15)))
    return launch


def run_unattended(
    config_path: str | Path,
    *,
    start_rtrader: bool = True,
    symbol: str | None = None,
) -> int:
    if is_windows():
        print(
            "ERROR: Rithmic trial capture runs on CHI404 only. "
            "Do not run capture/unattended on a Windows workstation.",
            file=sys.stderr,
        )
        return 1

    cfg = load_config(config_path)
    if not cfg.enabled:
        logging.info("Rithmic trial lane disabled (enabled: false in %s) — clean exit", config_path)
        return 0

    unattended_cfg = cfg.unattended or {}
    poll = float(unattended_cfg.get("poll_interval_sec", 2.0))
    process_every = float(unattended_cfg.get("process_interval_sec", 300))
    manifest_every = float(unattended_cfg.get("manifest_interval_sec", 60))
    log_file = Path(unattended_cfg.get("log_file", "logs/rithmic_trial/unattended.log"))
    if not log_file.is_absolute():
        log_file = cfg.repo_root / log_file

    _setup_logging(log_file)
    sym = symbol or cfg.symbol
    stop = False

    def _handle_sig(*_args: object) -> None:
        nonlocal stop
        stop = True
        logging.info("Shutdown signal received")

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    if start_rtrader and cfg.connector.lower().startswith("rtrader"):
        if os.environ.get("RTRADER_START_WINE", "1").lower() not in ("0", "false", "no"):
            ensure_rtrader_started(cfg)
        else:
            logging.info("RTRADER_START_WINE=0 — expecting log_push bridge into watch_dirs")

    connector = build_connector(cfg)
    connector.connect()
    capture = LiveCapture(cfg, date=_utc_date(), symbol=sym)
    total = 0
    market_total = 0
    market_types = {"trade", "quote"}
    last_process = 0.0
    last_manifest = 0.0
    started = time.time()

    logging.info(
        "Unattended capture started symbol=%s env=%s gateway=%s poll=%ss",
        sym,
        cfg.rithmic.get("environment", "Rithmic Paper Trading"),
        cfg.rithmic.get("gateway", "Chicago"),
        poll,
    )

    try:
        while not stop:
            batch = connector.poll_events()
            if batch:
                market_batch = [
                    ev for ev in batch if str(ev.get("event_type", "")).lower() in market_types
                ]
                if market_batch:
                    market_total += capture.append_raw(market_batch)
                total += len(batch)
            now = time.time()

            if now - last_manifest >= manifest_every:
                lim = connector.limitations()
                capture.finalize(
                    connector.detected_event_types(),
                    lim.get("missing_event_types", []),
                    lim,
                )
                last_manifest = now
                _write_status(
                    cfg.reports_dir(_utc_date()),
                    {
                        "status": "running",
                        "events_captured": market_total,
                        "events_polled": total,
                        "uptime_sec": int(now - started),
                        "detected_event_types": sorted(connector.detected_event_types()),
                        "watch_dirs": lim.get("watch_dirs", []),
                    },
                )

            if now - last_process >= process_every and market_total > 0:
                logging.info("Running periodic process pass")
                args = type(
                    "Args",
                    (),
                    {
                        "config": str(config_path),
                        "date": _utc_date(),
                        "symbol": sym,
                    },
                )()
                cmd_process(args)
                last_process = now

            time.sleep(poll)
    finally:
        lim = connector.limitations()
        capture.finalize(
            connector.detected_event_types(),
            lim.get("missing_event_types", []),
            lim,
        )
        connector.close()
        _write_status(
            cfg.reports_dir(_utc_date()),
            {
                "status": "stopped",
                "events_captured": market_total,
                "events_polled": total,
                "uptime_sec": int(time.time() - started),
            },
        )
        logging.info(
            "Unattended capture stopped; market events=%s polled=%s",
            market_total,
            total,
        )

    return 0
