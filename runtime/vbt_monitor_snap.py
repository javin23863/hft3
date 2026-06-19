#!/usr/bin/env python3
import re, json, os, subprocess
from datetime import datetime, timezone
from collections import defaultdict

EXPECTED = 1629250
RUN_START = datetime(2026, 6, 18, 16, 51, 8, tzinfo=timezone.utc)
ORCH = "/root/hft3/repo/research_cards/pipeline_runs/paid_full_20260618T165108Z/orchestrator.log"
LOG = "/root/vbt_full.log"
UNITS_ROOT = "/root/hft3/repo/research_cards/pipeline_runs/paid_full_20260618T165108Z/units"
MONITOR = "/root/vbt_full_monitor.jsonl"

pat = re.compile(r"\[unit\] (\S+) .*-> OK \(([0-9.]+)s\)")
md = defaultdict(list)
ed = defaultdict(list)
for path in (ORCH,):
    if os.path.isfile(path):
        with open(path) as f:
            for line in f:
                m = pat.search(line)
                if not m:
                    continue
                uid, dur = m.group(1), float(m.group(2))
                parts = uid.split("_")
                sym_i = next((i for i, p in enumerate(parts) if ".v.0" in p), 3)
                model = "_".join(parts[:sym_i])
                rest = "_".join(parts[sym_i + 1 :])
                ev = re.sub(r"_\d{4}_\d{2}_\d{2}_TIGHT$", "", rest)
                md[model].append(dur)
                ed[ev].append(dur)

art = sum(
    1
    for dp, _, fs in os.walk(UNITS_ROOT)
    for fn in fs
    if fn == "screening_artifact.json"
)
now = datetime.now(timezone.utc)
el_min = (now - RUN_START).total_seconds() / 60.0
rate_min = art / el_min if el_min > 0 else 0.0
remain = EXPECTED - art
eta_h = (remain / rate_min / 60.0) if rate_min > 0 else None

def top(d, n=8):
    rows = [(k, len(v), round(sum(v) / len(v), 2)) for k, v in d.items()]
    rows.sort(key=lambda x: -x[1])
    return rows[:n]

try:
    load = open("/proc/loadavg").read().split()[:3]
except OSError:
    load = []
workers = int(subprocess.check_output(["pgrep", "-fc", "run_pipeline"], text=True).strip() or "0")
tmux_ok = subprocess.run(["tmux", "has-session", "-t", "vbt_full"], capture_output=True).returncode == 0
log_mtime = os.path.getmtime(LOG) if os.path.isfile(LOG) else None
stall_min = (now.timestamp() - log_mtime) / 60.0 if log_mtime else None

rec = {
    "ts_utc": now.isoformat(),
    "tmux": tmux_ok,
    "ok_count": art,
    "expected": EXPECTED,
    "pct": round(100.0 * art / EXPECTED, 4),
    "workers": workers,
    "loadavg": load,
    "log_mtime_utc": datetime.fromtimestamp(log_mtime, tz=timezone.utc).isoformat() if log_mtime else None,
    "log_stall_min": round(stall_min, 2) if stall_min is not None else None,
    "rate_per_min_overall": round(rate_min, 2),
    "rate_per_hr_overall": round(rate_min * 60, 0),
    "eta_hours_overall": round(eta_h, 1) if eta_h else None,
    "fail_log": int(subprocess.check_output(f"grep -ci fail {ORCH} || true", shell=True, text=True).strip() or 0),
    "traceback_log": int(subprocess.check_output(f"grep -ci traceback {ORCH} || true", shell=True, text=True).strip() or 0),
    "top_models": top(md),
    "top_events": top(ed),
}
anomalies = []
if not tmux_ok and art < EXPECTED:
    anomalies.append("tmux_missing_incomplete")
if stall_min and stall_min > 30 and workers > 0:
    anomalies.append("no_ok_stall_30m")
if rec["traceback_log"] > 0:
    anomalies.append("traceback")
if rec["fail_log"] > 0:
    anomalies.append("failed_units_in_log")
rec["anomalies"] = anomalies
rec["anomaly_severity"] = "warning" if (stall_min and stall_min > 10 and workers > 0) else "none"
if stall_min and stall_min > 30 and workers > 0:
    rec["anomaly_severity"] = "critical"

with open(MONITOR, "a") as out:
    out.write(json.dumps(rec) + "\n")
print(json.dumps(rec, indent=2))
