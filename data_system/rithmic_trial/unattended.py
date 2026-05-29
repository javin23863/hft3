from __future__ import annotations

import json
import logging
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
from .windows_paths import discover_rtrader_exe, is_windows


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


def _rtrader_running(exe_name: str) -> bool:
    if not is_windows():
        return False
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return exe_name.lower() in out.lower()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def ensure_rtrader_started(cfg: TrialConfig) -> Path | None:
    rt = cfg.rtrader
    exe = Path(rt.get("exe_path", "")) if rt.get("exe_path") else discover_rtrader_exe()
    if exe is None or not exe.exists():
        logging.warning("R|Trader exe not found; set RTRADER_EXE_PATH or rtrader.exe_path in config")
        return None

    if rt.get("skip_start_if_running", True) and _rtrader_running(exe.name):
        logging.info("R|Trader already running: %s", exe.name)
        return exe

    args = [str(exe), *rt.get("extra_args", [])]
    logging.info("Starting R|Trader minimized: %s", exe)
    if is_windows():
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        subprocess.Popen(
            args,
            cwd=str(exe.parent),
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(args, cwd=str(exe.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(float(rt.get("startup_wait_sec", 15)))
    return exe


def run_unattended(
    config_path: str | Path,
    *,
    start_rtrader: bool = True,
    symbol: str | None = None,
) -> int:
    cfg = load_config(config_path)
    if not cfg.enabled:
        logging.error("Rithmic trial lane disabled")
        return 1

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
        ensure_rtrader_started(cfg)

    connector = build_connector(cfg)
    connector.connect()
    capture = LiveCapture(cfg, date=_utc_date(), symbol=sym)
    total = 0
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
                total += capture.append_raw(batch)
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
                        "events_captured": total,
                        "uptime_sec": int(now - started),
                        "detected_event_types": sorted(connector.detected_event_types()),
                        "watch_dirs": lim.get("watch_dirs", []),
                    },
                )

            if now - last_process >= process_every and total > 0:
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
                "events_captured": total,
                "uptime_sec": int(time.time() - started),
            },
        )
        logging.info("Unattended capture stopped; total events=%s", total)

    return 0
