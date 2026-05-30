#!/usr/bin/env python3
"""Unified round-trip latency probe: loopback, ping, TCP, trial profile, optional CHI404.

Workstation RTT tiers here are diagnostic only — not part of the live execution path
(BLUEPRINT §4: CHI404 colo is self-sufficient for capture and orders).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import socket
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backtest_pipeline.src.runner import LATENCY_BANDS_MS

DEFAULT_SAMPLES = 20
DEFAULT_OUTPUT_DIR = "logs/roundtrip_speedtest"
PASS_CRITERIA_PATH = _REPO_ROOT / "infrastructure" / "chi404" / "PASS_CRITERIA.json"
LOOPBACK_HOST = "127.0.0.1"
RITHMIC_TCP_PORT = 65000
HTTPS_TCP_PORT = 443


def _load_dotenv(repo_root: Path) -> None:
    env_path = repo_root / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except ImportError:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


def _stats_ms(samples_ms: list[float]) -> dict[str, float | int | None]:
    if not samples_ms:
        return {"count": 0, "min_ms": None, "avg_ms": None, "max_ms": None, "stdev_ms": None}
    return {
        "count": len(samples_ms),
        "min_ms": min(samples_ms),
        "avg_ms": statistics.mean(samples_ms),
        "max_ms": max(samples_ms),
        "stdev_ms": statistics.stdev(samples_ms) if len(samples_ms) > 1 else 0.0,
    }


def measure_loopback_tcp(samples: int) -> dict[str, Any]:
    latencies: list[float] = []
    errors: list[str] = []

    for _ in range(samples):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((LOOPBACK_HOST, 0))
            sock.listen(1)
            port = sock.getsockname()[1]

            accepted: list[socket.socket] = []
            ready = threading.Event()

            def accept_one() -> None:
                conn, _ = sock.accept()
                accepted.append(conn)
                ready.set()

            t = threading.Thread(target=accept_one, daemon=True)
            t.start()
            t0 = time.perf_counter()
            client = socket.create_connection((LOOPBACK_HOST, port), timeout=2.0)
            ready.wait(timeout=2.0)
            latencies.append((time.perf_counter() - t0) * 1000.0)
            client.close()
            for c in accepted:
                c.close()
            t.join(timeout=1.0)
        except OSError as exc:
            errors.append(str(exc))
        finally:
            sock.close()

    out: dict[str, Any] = {"target": f"{LOOPBACK_HOST}:ephemeral", **_stats_ms(latencies)}
    if errors:
        out["errors"] = errors[:3]
    return out


def _ping_cmd(host: str, samples: int) -> list[str]:
    if platform.system().lower() == "windows":
        return ["ping", "-n", str(samples), "-w", "1000", host]
    return ["ping", "-c", str(samples), "-i", "0.2", "-q", host]


def _parse_ping_output(host: str, text: str) -> dict[str, Any]:
    linux = re.search(
        r"rtt min/avg/max/(?:mdev|stddev) = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)\s*ms",
        text,
    )
    if linux:
        return {
            "host": host,
            "min_ms": float(linux.group(1)),
            "avg_ms": float(linux.group(2)),
            "max_ms": float(linux.group(3)),
            "mdev_ms": float(linux.group(4)),
        }
    win = re.search(
        r"Minimum = (\d+)ms.*Maximum = (\d+)ms.*Average = (\d+)ms",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if win:
        mn, mx, avg = (float(win.group(i)) for i in range(1, 4))
        return {"host": host, "min_ms": mn, "avg_ms": avg, "max_ms": mx, "mdev_ms": None}
    return {"host": host, "error": "unparsed ping output", "raw": text.strip()[-500:]}


def measure_ping(host: str, samples: int) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            _ping_cmd(host, samples),
            capture_output=True,
            text=True,
            timeout=max(60, samples * 2),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"host": host, "error": str(exc)}
    text = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode not in (0, 1):
        return {"host": host, "error": f"ping exit {proc.returncode}", "raw": text.strip()[-500:]}
    parsed = _parse_ping_output(host, text)
    parsed["returncode"] = proc.returncode
    return parsed


def measure_tcp_connect(host: str, port: int, samples: int) -> dict[str, Any]:
    latencies: list[float] = []
    errors: list[str] = []
    for _ in range(samples):
        t0 = time.perf_counter()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        try:
            sock.connect((host, port))
            latencies.append((time.perf_counter() - t0) * 1000.0)
        except OSError as exc:
            errors.append(str(exc))
        finally:
            sock.close()
    out: dict[str, Any] = {
        "target": f"{host}:{port}",
        **_stats_ms(latencies),
    }
    if errors:
        out["errors"] = list(dict.fromkeys(errors))[:3]
    return out


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _latest_latency_profile(repo_root: Path) -> dict[str, Any] | None:
    from scripts.latency_probe.trial_profile import latest_latency_profile

    return latest_latency_profile(repo_root)


def _parse_chi404_tuning_block(text: str) -> dict[str, Any] | None:
    run_id: str | None = None
    tuning_dir: str | None = None
    pass_fail: str | None = None
    latency_summary: dict[str, Any] | None = None
    jitter_gate: str | None = None
    cyclictest: dict[str, int] = {}

    m = re.search(r"^CHI404_RUN_ID=(.+)$", text, re.MULTILINE)
    if m:
        run_id = m.group(1).strip()
    m = re.search(r"^CHI404_TUNING_DIR=(.+)$", text, re.MULTILINE)
    if m:
        tuning_dir = m.group(1).strip()
        if not run_id:
            run_id = Path(tuning_dir.rstrip("/")).name

    pf = re.search(
        r"CHI404_PASS_FAIL_BEGIN\n(.*?)CHI404_PASS_FAIL_END",
        text,
        re.DOTALL,
    )
    if pf:
        pass_fail = pf.group(1).strip()

    ls = re.search(
        r"CHI404_LATENCY_SUMMARY_BEGIN\n(.*?)CHI404_LATENCY_SUMMARY_END",
        text,
        re.DOTALL,
    )
    if ls:
        try:
            latency_summary = json.loads(ls.group(1).strip())
        except json.JSONDecodeError:
            latency_summary = {"error": "invalid latency_summary.json", "raw": ls.group(1).strip()[:500]}

    jg = re.search(
        r"CHI404_JITTER_GATE_BEGIN\n(.*?)CHI404_JITTER_GATE_END",
        text,
        re.DOTALL,
    )
    if jg:
        jitter_gate = jg.group(1).strip()

    for path_m, val_m in re.findall(
        r"cyclictest_cpu(\d+)_p99_us\n(\d+)",
        text,
    ):
        cyclictest[path_m] = int(val_m)

    if not run_id and not pass_fail and not latency_summary and not cyclictest:
        return None

    pass_ok = bool(pass_fail and pass_fail.splitlines()[0].strip() == "PASS")
    jitter_pass: bool | None = None
    if jitter_gate is not None:
        jitter_pass = bool(re.search(r"^JITTER_GATE=PASS\b", jitter_gate, re.MULTILINE))
    elif cyclictest:
        limit = 20
        crit = _load_json(PASS_CRITERIA_PATH)
        if crit and "cyclictest_p99_max_us" in crit:
            limit = int(crit["cyclictest_p99_max_us"])
        jitter_pass = all(v <= limit for v in cyclictest.values())

    return {
        "run_id": run_id,
        "tuning_dir": tuning_dir,
        "pass": pass_ok,
        "pass_fail": pass_fail,
        "cyclictest_p99_us": cyclictest,
        "latency_summary": latency_summary,
        "jitter_pass": jitter_pass,
    }


def _remote_chi404(ssh_host: str, samples: int) -> dict[str, Any]:
    tuning_fetch = (
        'TBASE="/root/hft3/logs/tuning"; TDIR=""; '
        'for d in $(ls -1dt "$TBASE"/*/ 2>/dev/null); do '
        '[ -f "$d/PASS_FAIL.txt" ] || continue; '
        'head -1 "$d/PASS_FAIL.txt" | grep -q "^PASS" || continue; '
        'TDIR="$d"; break; done; '
        'if [ -n "$TDIR" ]; then '
        'echo "CHI404_TUNING_DIR=$TDIR"; '
        'echo "CHI404_RUN_ID=$(basename "$TDIR")"; '
        'echo "CHI404_PASS_FAIL_BEGIN"; cat "$TDIR/PASS_FAIL.txt" 2>/dev/null || true; '
        'echo "CHI404_PASS_FAIL_END"; '
        'if [ -f "$TDIR/latency_summary.json" ]; then '
        'echo "CHI404_LATENCY_SUMMARY_BEGIN"; cat "$TDIR/latency_summary.json"; '
        'echo "CHI404_LATENCY_SUMMARY_END"; fi; '
        'if [ -f "$TDIR/jitter_gate_result" ]; then '
        'echo "CHI404_JITTER_GATE_BEGIN"; cat "$TDIR/jitter_gate_result"; '
        'echo "CHI404_JITTER_GATE_END"; fi; '
        'find "$TDIR" -maxdepth 1 -name "cyclictest_cpu*_p99_us" -print -exec cat {} \\; 2>/dev/null; '
        "fi"
    )
    ping_block = (
        f"GW=${{HFT3_GATEWAY_IP:-}}; RH=${{HFT3_RITHMIC_HOST:-}}; "
        f"for H in $GW $RH; do "
        f'[ -n "$H" ] && echo "PING_HOST=$H" && ping -c {samples} -i 0.2 -q "$H" 2>&1 || true; '
        f"done; "
        f"{tuning_fetch}; "
        f'LOG="${{HFT3_TUNING_LOG_DIR:-}}"; '
        f'if [ -n "$LOG" ]; then find "$LOG" -maxdepth 1 -name "cyclictest_cpu*_p99_us" -print -exec cat {{}} \\; 2>/dev/null; fi'
    )
    cmd = f"set -a; [ -f /root/hft3/.env ] && . /root/hft3/.env; set +a; {ping_block}"
    try:
        proc = subprocess.run(
            ["ssh", ssh_host, cmd],
            capture_output=True,
            text=True,
            timeout=max(90, samples * 3),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ssh_host": ssh_host, "error": str(exc)}
    text = (proc.stdout or "") + (proc.stderr or "")
    pings: list[dict[str, Any]] = []
    cyclictest: dict[str, int] = {}
    lines = text.splitlines()
    current_host: str | None = None
    buf: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("PING_HOST="):
            if current_host and buf:
                pings.append(_parse_ping_output(current_host, "\n".join(buf)))
            current_host = line.split("=", 1)[1].strip()
            buf = []
            i += 1
            continue
        path_m = re.search(r"cyclictest_cpu(\d+)_p99_us$", line)
        if path_m:
            if i + 1 < len(lines) and re.fullmatch(r"\d+", lines[i + 1].strip()):
                cyclictest[path_m.group(1)] = int(lines[i + 1].strip())
                i += 2
                continue
            i += 1
            continue
        if current_host is not None:
            buf.append(line)
        i += 1
    if current_host and buf:
        pings.append(_parse_ping_output(current_host, "\n".join(buf)))

    chi404_tuning = _parse_chi404_tuning_block(text)
    if chi404_tuning and cyclictest:
        merged = {**(chi404_tuning.get("cyclictest_p99_us") or {}), **cyclictest}
        chi404_tuning = {**chi404_tuning, "cyclictest_p99_us": merged}
        if chi404_tuning.get("jitter_pass") is None and merged:
            limit = 20
            crit = _load_json(PASS_CRITERIA_PATH)
            if crit and "cyclictest_p99_max_us" in crit:
                limit = int(crit["cyclictest_p99_max_us"])
            chi404_tuning["jitter_pass"] = all(v <= limit for v in merged.values())

    out: dict[str, Any] = {
        "ssh_host": ssh_host,
        "returncode": proc.returncode,
        "pings": pings,
        "cyclictest_p99_us": cyclictest,
        "raw_tail": text.strip()[-800:] if proc.returncode != 0 and not pings else None,
    }
    if chi404_tuning:
        out["chi404_tuning"] = chi404_tuning
    return out


def _configured_hosts() -> list[tuple[str, str]]:
    hosts: list[tuple[str, str]] = []
    for label, key in (
        ("gateway", "HFT3_GATEWAY_IP"),
        ("rithmic", "HFT3_RITHMIC_HOST"),
        ("chi404", "CHI404_HOST"),
    ):
        val = (os.environ.get(key) or "").strip()
        if val:
            hosts.append((label, val))
    return hosts


def _classify_tier(dominant_ms: float | None) -> dict[str, Any]:
    if dominant_ms is None:
        return {
            "tier_ms": None,
            "tier_name": "unknown",
            "recommended_latency_bands_ms": list(LATENCY_BANDS_MS),
            "recommendation": "No RTT measured; use full LATENCY_BANDS_MS sweep in backtest.",
        }
    max_band = LATENCY_BANDS_MS[-1]
    if dominant_ms > max_band:
        return {
            "tier_ms": None,
            "tier_name": "tier_retail_remote",
            "recommended_latency_bands_ms": list(LATENCY_BANDS_MS),
            "recommendation": (
                f"RTT {dominant_ms:.3f} ms exceeds blueprint max band ({max_band:g} ms); "
                "not colo/HFT tier — backtest at 5-10 ms+ only."
            ),
        }
    tier = max_band
    for band in LATENCY_BANDS_MS:
        if dominant_ms <= band:
            tier = band
            break
    bands = [b for b in LATENCY_BANDS_MS if b <= tier]
    if not bands:
        bands = [tier]
    return {
        "tier_ms": tier,
        "tier_name": f"tier_{tier:g}ms",
        "recommended_latency_bands_ms": bands,
        "recommendation": (
            f"Use LATENCY_BANDS_MS subset {bands} (dominant RTT {dominant_ms:.3f} ms <= {tier:g} ms tier)."
        ),
    }


def _min_ping_avg(items: list[dict[str, Any]] | None, labels: tuple[str, ...]) -> float | None:
    avgs: list[float] = []
    for item in items or []:
        if item.get("label") not in labels:
            continue
        avg = item.get("avg_ms")
        if isinstance(avg, (int, float)):
            avgs.append(float(avg))
    return min(avgs) if avgs else None


def _min_remote_ping_avg(remote: dict[str, Any] | None) -> float | None:
    avgs: list[float] = []
    for item in (remote or {}).get("pings") or []:
        avg = item.get("avg_ms")
        if isinstance(avg, (int, float)):
            avgs.append(float(avg))
    return min(avgs) if avgs else None


def _classification_block(dominant_ms: float | None, bottleneck: str) -> dict[str, Any]:
    block: dict[str, Any] = {"dominant_rtt_ms": dominant_ms, "bottleneck": bottleneck}
    block.update(_classify_tier(dominant_ms))
    return block


def _build_classifications(payload: dict[str, Any]) -> dict[str, Any]:
    ping_hosts = (payload.get("ping") or {}).get("hosts") or []
    remote = payload.get("remote")
    trial = payload.get("rithmic_trial_profile") or {}
    colo_only = bool(payload.get("colo_only"))

    local_ms = _min_ping_avg(ping_hosts, ("chi404", "rithmic"))
    local_workstation = _classification_block(local_ms, "min_ping_chi404_rithmic")

    colo_ms = _min_remote_ping_avg(remote) if remote else None
    colo_on_box = _classification_block(colo_ms, "min_remote_ping_gateway_rithmic")

    order_ms: float | None = None
    if trial.get("trusted") and isinstance(trial.get("order_rtt_ms"), (int, float)):
        order_ms = float(trial["order_rtt_ms"])
    order_path = _classification_block(order_ms, "order_rtt_ms")

    if colo_only and remote:
        if colo_ms is not None:
            operating_tier = {**colo_on_box, "source": "colo_on_box"}
        else:
            operating_tier = {
                "status": "pending",
                "reason": "colo-only remote probe: no gateway/rithmic ping on CHI404",
            }
    elif local_ms is not None and local_ms > 5.0:
        operating_tier = {**local_workstation, "source": "local_workstation"}
    elif colo_ms is not None:
        operating_tier = {**colo_on_box, "source": "colo_on_box"}
    else:
        operating_tier = {**order_path, "source": "order_path"}

    return {
        "local_workstation": local_workstation,
        "colo_on_box": colo_on_box,
        "order_path": order_path,
        "operating_tier": operating_tier,
    }


def _cyclictest_p99_max(cyclictest: dict[str, int] | None) -> int | None:
    if not cyclictest:
        return None
    return max(cyclictest.values())


def _gateway_rtt_from_summary(latency_summary: dict[str, Any] | None) -> float | None:
    if not latency_summary:
        return None
    gw = latency_summary.get("gateway_ping")
    if isinstance(gw, dict):
        avg = gw.get("avg_ms")
        if isinstance(avg, (int, float)):
            return float(avg)
    return None


def _pass_criteria_p99_limit() -> int:
    crit = _load_json(PASS_CRITERIA_PATH)
    if crit and "cyclictest_p99_max_us" in crit:
        return int(crit["cyclictest_p99_max_us"])
    return 20


def _build_complete_picture(payload: dict[str, Any]) -> dict[str, Any]:
    p99_limit = _pass_criteria_p99_limit()
    remote = payload.get("remote") or {}
    tuning = remote.get("chi404_tuning") or {}
    classifications = payload.get("classifications") or {}
    trial = payload.get("rithmic_trial_profile") or {}

    ping_hosts = (payload.get("ping") or {}).get("hosts") or []
    chi404_ping = next((h for h in ping_hosts if h.get("label") == "chi404"), None)
    ws_rtt: float | None = None
    if chi404_ping and isinstance(chi404_ping.get("avg_ms"), (int, float)):
        ws_rtt = float(chi404_ping["avg_ms"])
    if ws_rtt is not None:
        workstation_to_colo: dict[str, Any] = {
            "status": "PASS",
            "rtt_ms": ws_rtt,
            "host": chi404_ping.get("host"),
        }
    elif chi404_ping and chi404_ping.get("error"):
        workstation_to_colo = {"status": "FAIL", "reason": chi404_ping["error"]}
    else:
        workstation_to_colo = {
            "status": "pending",
            "reason": "CHI404_HOST not configured or ping failed",
        }

    cyclictest: dict[str, int] = {}
    if isinstance(tuning.get("cyclictest_p99_us"), dict):
        cyclictest.update({k: int(v) for k, v in tuning["cyclictest_p99_us"].items()})
    if isinstance(remote.get("cyclictest_p99_us"), dict):
        cyclictest.update({k: int(v) for k, v in remote["cyclictest_p99_us"].items()})
    max_p99 = _cyclictest_p99_max(cyclictest or None)
    if max_p99 is not None:
        jitter_ok = max_p99 <= p99_limit
        chi404_kernel_jitter: dict[str, Any] = {
            "status": "PASS" if jitter_ok else "FAIL",
            "max_p99_us": max_p99,
            "limit_us": p99_limit,
            "cyclictest_p99_us": cyclictest,
        }
    else:
        chi404_kernel_jitter = {
            "status": "pending",
            "reason": "no cyclictest p99 data from CHI404",
        }

    latency_summary = tuning.get("latency_summary")
    gw_rtt = _gateway_rtt_from_summary(latency_summary if isinstance(latency_summary, dict) else None)
    gw_source = "latency_summary"
    if gw_rtt is None:
        gw_rtt = _min_remote_ping_avg(remote)
        gw_source = "remote_ping"
    if gw_rtt is not None:
        chi404_gateway_rtt: dict[str, Any] = {
            "status": "PASS",
            "rtt_ms": gw_rtt,
            "source": gw_source,
        }
    else:
        chi404_gateway_rtt = {
            "status": "pending",
            "reason": "no gateway RTT from CHI404 remote probe",
        }

    if trial.get("trusted") and isinstance(trial.get("order_rtt_ms"), (int, float)):
        order_submit_ack: dict[str, Any] = {
            "status": "PASS",
            "order_rtt_ms": float(trial["order_rtt_ms"]),
            "profile": trial.get("path"),
        }
    elif trial:
        reason = "profile marked untrusted"
        if trial.get("connector") == "fixture":
            reason = "synthetic fixture connector"
        order_submit_ack = {
            "status": "not_measured",
            "reason": reason,
            "profile": trial.get("path"),
        }
    else:
        order_submit_ack = {
            "status": "not_measured",
            "reason": "no rithmic_trial latency_profile.json",
        }

    chi404_pass = tuning.get("pass") is True
    if chi404_pass and gw_rtt is not None:
        production_operating_tier: dict[str, Any] = {
            "status": "PASS",
            "tier_ms": 0.5,
            "tier_name": "tier_0.5ms",
            "recommended_latency_bands_ms": [0.5],
            "gateway_rtt_ms": gw_rtt,
        }
    elif tuning and tuning.get("pass") is False:
        production_operating_tier = {
            "status": "FAIL",
            "reason": "CHI404 tuning PASS gate not satisfied",
        }
    elif gw_rtt is None:
        production_operating_tier = {"status": "pending", "reason": "gateway RTT unknown"}
    else:
        production_operating_tier = {
            "status": "pending",
            "reason": "CHI404 tuning PASS status unknown",
        }

    local_ws = classifications.get("local_workstation") or {}
    if payload.get("colo_only"):
        research_operating_tier = {
            "status": "skipped",
            "reason": "--colo-only with --remote",
        }
    elif local_ws.get("dominant_rtt_ms") is not None:
        research_operating_tier: dict[str, Any] = {
            **local_ws,
            "status": "PASS",
            "source": "local_workstation",
        }
    else:
        research_operating_tier = {
            **local_ws,
            "status": "pending",
            "reason": "workstation RTT not measured",
        }

    return {
        "timestamp_utc": payload.get("timestamp_utc"),
        "chi404_tuning_run_id": tuning.get("run_id"),
        "legs": {
            "workstation_to_colo": workstation_to_colo,
            "chi404_kernel_jitter": chi404_kernel_jitter,
            "chi404_gateway_rtt": chi404_gateway_rtt,
            "order_submit_ack": order_submit_ack,
            "production_operating_tier": production_operating_tier,
            "research_operating_tier": research_operating_tier,
        },
    }


def _format_leg_status(leg: dict[str, Any]) -> str:
    status = leg.get("status", "pending")
    if status == "PASS":
        if "rtt_ms" in leg:
            return f"PASS ({leg['rtt_ms']:.3f} ms)"
        if "order_rtt_ms" in leg:
            return f"PASS ({leg['order_rtt_ms']:.3f} ms order RTT)"
        if "max_p99_us" in leg:
            return f"PASS (max p99 {leg['max_p99_us']} us <= {leg.get('limit_us')} us)"
        if leg.get("dominant_rtt_ms") is not None:
            return f"PASS ({leg['dominant_rtt_ms']:.3f} ms -> {leg.get('tier_name', 'unknown')})"
        if "tier_name" in leg:
            return f"PASS ({leg['tier_name']}, bands {leg.get('recommended_latency_bands_ms')})"
        return "PASS"
    if status == "FAIL":
        return f"FAIL ({leg.get('reason', leg.get('max_p99_us', 'check leg'))})"
    if status == "not_measured":
        return f"not_measured ({leg.get('reason', 'unknown')})"
    if status == "skipped":
        return f"skipped ({leg.get('reason', 'unknown')})"
    return f"pending ({leg.get('reason', 'unknown')})"


def _skipped_local_leg(reason: str) -> dict[str, Any]:
    return {"status": "skipped", "reason": reason}


def run_probe(
    repo_root: Path,
    samples: int,
    output_dir: Path,
    remote: str | None,
    *,
    colo_only: bool = False,
) -> dict[str, Any]:
    _load_dotenv(repo_root)
    skip_local = colo_only and remote is not None
    hosts = [] if skip_local else _configured_hosts()

    if skip_local:
        loopback = _skipped_local_leg("--colo-only with --remote")
        ping_results = []
        tcp_targets = []
    else:
        loopback = measure_loopback_tcp(samples)
        ping_results = []
        for label, host in hosts:
            row = measure_ping(host, samples)
            row["label"] = label
            ping_results.append(row)

        tcp_targets = []
        for label, host in hosts:
            if label == "rithmic":
                port = RITHMIC_TCP_PORT
            else:
                port = HTTPS_TCP_PORT
            row = measure_tcp_connect(host, port, samples)
            row["label"] = label
            tcp_targets.append(row)

    trial = _latest_latency_profile(repo_root)
    remote_block: dict[str, Any] | None = None
    if remote:
        ssh_host = os.environ.get("CHI404_SSH_ALIAS", remote)
        remote_block = _remote_chi404(ssh_host, samples)

    payload: dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "samples": samples,
        "colo_only": colo_only,
        "latency_bands_ms": list(LATENCY_BANDS_MS),
        "env_hosts": {label: host for label, host in hosts},
        "loopback_tcp": loopback,
        "ping": {"hosts": ping_results},
        "tcp_connect": {"targets": tcp_targets},
        "rithmic_trial_profile": trial,
        "remote": remote_block,
    }

    classifications = _build_classifications(payload)
    payload["classifications"] = classifications
    operating = classifications["operating_tier"]
    payload["dominant_rtt_ms"] = operating.get("dominant_rtt_ms")
    payload["bottleneck"] = operating.get("bottleneck")
    payload["classification"] = {
        k: operating[k]
        for k in ("tier_ms", "tier_name", "recommended_latency_bands_ms", "recommendation")
        if k in operating
    }

    complete_picture = _build_complete_picture(payload)
    payload["complete_picture"] = complete_picture

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "latest.json"
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    picture_path = output_dir / "complete_picture.json"
    picture_path.write_text(json.dumps(complete_picture, indent=2) + "\n", encoding="utf-8")
    payload["output_path"] = str(out_path.relative_to(repo_root)).replace("\\", "/")
    payload["complete_picture_path"] = str(picture_path.relative_to(repo_root)).replace("\\", "/")
    return payload


def _print_summary(payload: dict[str, Any]) -> None:
    classifications = payload.get("classifications") or {}
    local_cls = classifications.get("local_workstation") or {}
    colo_cls = classifications.get("colo_on_box") or {}
    cls = payload["classification"]
    operating = classifications.get("operating_tier") or {}

    local_ms = local_cls.get("dominant_rtt_ms")
    local_s = f"{local_ms:.3f}" if local_ms is not None else "n/a"
    colo_ms = colo_cls.get("dominant_rtt_ms")
    colo_s = f"{colo_ms:.3f}" if colo_ms is not None else "n/a"

    print("=== Round-trip speedtest ===")
    print(f"Platform: {payload.get('platform')}")
    print(f"Samples:  {payload.get('samples')}")
    print(
        f"Trading from THIS machine: {local_s} ms -> tier {local_cls.get('tier_name', 'unknown')}"
    )
    print(f"CHI404 colo gateway: {colo_s} ms -> tier {colo_cls.get('tier_name', 'unknown')}")
    dom = payload.get("dominant_rtt_ms")
    dom_s = f"{dom:.3f}" if dom is not None else "n/a"
    print(
        f"Operating tier ({operating.get('source', 'unknown')}): {dom_s} ms -> "
        f"{cls.get('tier_name')}  ({cls.get('tier_ms')} ms band)"
    )
    print(f"Bottleneck metric: {payload.get('bottleneck')}")
    print(f"Backtest bands: {cls.get('recommended_latency_bands_ms')}")
    print(f"Note: {cls.get('recommendation')}")
    lb = payload.get("loopback_tcp") or {}
    print(f"Loopback TCP avg: {lb.get('avg_ms')} ms")
    for item in (payload.get("ping") or {}).get("hosts") or []:
        print(f"Ping [{item.get('label')}] {item.get('host')}: avg={item.get('avg_ms')} ms")
    for item in (payload.get("tcp_connect") or {}).get("targets") or []:
        print(f"TCP  [{item.get('label')}] {item.get('target')}: avg={item.get('avg_ms')} ms")
    trial = payload.get("rithmic_trial_profile")
    if trial:
        print(
            f"Trial profile: {trial.get('path')} trusted={trial.get('trusted')} "
            f"order_rtt_ms={trial.get('order_rtt_ms')}"
        )
    if payload.get("remote"):
        print(f"Remote SSH: {payload['remote'].get('ssh_host')} (rc={payload['remote'].get('returncode')})")
        tuning = (payload["remote"] or {}).get("chi404_tuning")
        if tuning:
            print(
                f"CHI404 tuning: run_id={tuning.get('run_id')} pass={tuning.get('pass')} "
                f"jitter_pass={tuning.get('jitter_pass')}"
            )

    picture = payload.get("complete_picture") or {}
    legs = picture.get("legs") or {}
    print("=== Complete latency picture ===")
    for name, leg in legs.items():
        print(f"  {name}: {_format_leg_status(leg)}")
    if payload.get("complete_picture_path"):
        print(f"Complete picture: {payload.get('complete_picture_path')}")
    print(f"Wrote: {payload.get('output_path')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Unified round-trip latency probe.")
    parser.add_argument(
        "--remote",
        metavar="HOST",
        nargs="?",
        const="chi404",
        default=None,
        help="SSH remote probe (default alias chi404 when flag alone)",
    )
    parser.add_argument(
        "--colo-only",
        action="store_true",
        help="With --remote: skip local loopback/ping/TCP (CHI404 remote metrics only)",
    )
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    repo_root = _REPO_ROOT
    out_dir = Path(args.output_dir)
    if not out_dir.is_absolute():
        out_dir = repo_root / out_dir

    if args.colo_only and not args.remote:
        parser.error("--colo-only requires --remote")

    payload = run_probe(
        repo_root,
        args.samples,
        out_dir,
        args.remote,
        colo_only=args.colo_only,
    )
    _print_summary(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
