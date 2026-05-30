#!/usr/bin/env bash
# CHI404 colo-only: gateway + Rithmic network probes. Run ON bare metal only.
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
RUN_ID="${RUN_ID:-$(cat "$REPO/runtime/latency_reports/raw/LATEST_RUN_ID" 2>/dev/null || date -u +%Y%m%dT%H%M%SZ)}"
REPORT_ROOT="${LATENCY_REPORT_ROOT:-$REPO/runtime/latency_reports}"
RAW="$REPORT_ROOT/raw/$RUN_ID"
SAMPLES="${LATENCY_PROBE_PING_SAMPLES:-100}"

mkdir -p "$RAW"

GW="${HFT3_GATEWAY_IP:-$(ip route | awk '/default/ {print $3; exit}')}"
RH="${HFT3_RITHMIC_HOST:-}"

echo "network_probe RUN_ID=$RUN_ID GW=${GW:-none} RH=${RH:-none} SAMPLES=$SAMPLES"

python3 - "$RAW/network.json" "$GW" "$RH" "$SAMPLES" <<'PY'
import json
import re
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

out_path, gw, rh, samples_s = sys.argv[1:5]
samples = int(samples_s)
RITHMIC_PORT = 65000
HTTPS_PORT = 443


def pct(vals, p):
    if not vals:
        return None
    vals = sorted(vals)
    idx = min(len(vals) - 1, max(0, int(p * len(vals)) - 1))
    return vals[idx]


def ping_series(host: str, count: int) -> dict:
    if not host:
        return {"host": host, "status": "skipped"}
    try:
        proc = subprocess.run(
            ["ping", "-c", str(count), "-i", "0.2", host],
            capture_output=True,
            text=True,
            timeout=max(60, count * 2),
        )
        text = proc.stdout + proc.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return {"host": host, "status": "error", "error": str(exc)}

    rtts_ms: list[float] = []
    for line in text.splitlines():
        m = re.search(r"time=([\d.]+)\s*ms", line)
        if m:
            rtts_ms.append(float(m.group(1)))

    summary = re.search(
        r"(\d+) packets transmitted, (\d+) received, ([\d.]+)% packet loss",
        text,
    )
    loss_pct = float(summary.group(3)) if summary else None
    transmitted = int(summary.group(1)) if summary else count
    received = int(summary.group(2)) if summary else len(rtts_ms)

    agg = re.search(
        r"rtt min/avg/max/(?:mdev|stddev) = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)\s*ms",
        text,
    )

    payload = {
        "host": host,
        "transmitted": transmitted,
        "received": received,
        "loss_pct": loss_pct,
        "samples": len(rtts_ms),
        "p50_ms": pct(rtts_ms, 0.50),
        "p95_ms": pct(rtts_ms, 0.95),
        "p99_ms": pct(rtts_ms, 0.99),
        "p999_ms": pct(rtts_ms, 0.999),
        "max_ms": max(rtts_ms) if rtts_ms else None,
        "avg_ms": statistics.mean(rtts_ms) if rtts_ms else None,
        "jitter_ms": statistics.pstdev(rtts_ms) if len(rtts_ms) > 1 else None,
    }
    if agg:
        payload["ping_summary"] = {
            "min_ms": float(agg.group(1)),
            "avg_ms": float(agg.group(2)),
            "max_ms": float(agg.group(3)),
            "mdev_ms": float(agg.group(4)),
        }
    if not rtts_ms and loss_pct == 100.0:
        payload["status"] = "icmp_blocked_or_unreachable"
    elif rtts_ms:
        payload["status"] = "ok"
    else:
        payload["status"] = "no_rtt_samples"
        payload["raw_tail"] = text.strip()[-500:]
    return payload


def tcp_connect_series(host: str, port: int, count: int) -> dict:
    if not host:
        return {"host": host, "port": port, "status": "skipped"}
    lat_ms: list[float] = []
    errors: list[str] = []
    for _ in range(count):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3.0)
        t0 = time.perf_counter()
        try:
            s.connect((host, port))
            lat_ms.append((time.perf_counter() - t0) * 1000.0)
        except OSError as exc:
            errors.append(str(exc))
        finally:
            s.close()
    return {
        "host": host,
        "port": port,
        "samples": len(lat_ms),
        "p50_ms": pct(lat_ms, 0.50),
        "p95_ms": pct(lat_ms, 0.95),
        "p99_ms": pct(lat_ms, 0.99),
        "p999_ms": pct(lat_ms, 0.999),
        "max_ms": max(lat_ms) if lat_ms else None,
        "avg_ms": statistics.mean(lat_ms) if lat_ms else None,
        "errors": errors[:3] if errors else None,
        "status": "ok" if lat_ms else "failed",
    }


payload = {
    "gateway_ping": ping_series(gw, samples),
    "rithmic_ping": ping_series(rh, samples) if rh else {"status": "not_configured"},
    "gateway_tcp_443": tcp_connect_series(gw, HTTPS_PORT, min(30, samples)) if gw else {"status": "skipped"},
    "rithmic_tcp_65000": tcp_connect_series(rh, RITHMIC_PORT, min(30, samples)) if rh else {"status": "not_configured"},
}

Path(out_path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(f"Wrote {out_path}")
PY

echo "network_probe done RUN_ID=$RUN_ID"
