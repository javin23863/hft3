#!/usr/bin/env python3
import json
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DECL_FILE = os.environ.get(
    "VBT_FULL_RUN_DECLARATION",
    "/root/hft3/repo/runtime/reports/vbt_full_run_declaration.json",
)
REPO_ROOT = Path(os.environ.get("HFT3_REPO", "/root/hft3/repo"))


def _load_declaration() -> dict:
    if not os.path.isfile(DECL_FILE):
        return {}
    with open(DECL_FILE, encoding="utf-8") as f:
        return json.load(f)


def _resolve_run_id(decl: dict) -> str:
    env_run = os.environ.get("VBT_FULL_RUN_ID", "").strip()
    if env_run:
        return env_run
    for key in ("vbt_full_run_id", "run_id"):
        value = str(decl.get(key) or "").strip()
        if value and "PENDING" not in value:
            return value
    return ""


def _run_paths(run_id: str) -> tuple[str, str, str, str]:
    run_dir = REPO_ROOT / "research_cards" / "pipeline_runs" / run_id
    orch = str(run_dir / "orchestrator.log")
    units_root = str(run_dir / "units")
    log = os.environ.get("VBT_FULL_LOG", orch)
    monitor = os.environ.get("VBT_MONITOR_JSONL", "/root/vbt_full_monitor.jsonl")
    return orch, units_root, log, monitor


def main() -> int:
    decl = _load_declaration()
    expected = decl.get("expected_work_units")
    run_id = _resolve_run_id(decl)
    if expected is None:
        print(json.dumps({"error": "expected_work_units missing from declaration", "decl": DECL_FILE}))
        return 1
    if not run_id:
        print(
            json.dumps(
                {
                    "error": "no active run_id (set VBT_FULL_RUN_ID or clear PENDING in declaration)",
                    "decl": DECL_FILE,
                }
            )
        )
        return 1

    expected = int(expected)
    orch, units_root, log, monitor = _run_paths(run_id)
    run_start = None
    manifest_path = Path(orch).parent / "paid_screen_run_manifest.json"
    if manifest_path.is_file():
        for key in ("started_at", "start_time", "created_at"):
            raw = (json.loads(manifest_path.read_text(encoding="utf-8")).get(key) or "").strip()
            if raw:
                run_start = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                break

    pat = re.compile(r"\[unit\] (\S+) .*-> OK \(([0-9.]+)s\)")
    md: defaultdict[str, list[float]] = defaultdict(list)
    ed: defaultdict[str, list[float]] = defaultdict(list)
    if os.path.isfile(orch):
        with open(orch, encoding="utf-8", errors="replace") as f:
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
        for _, _, fs in os.walk(units_root)
        for fn in fs
        if fn == "screening_artifact.json"
    )
    now = datetime.now(timezone.utc)
    el_min = (now - run_start).total_seconds() / 60.0 if run_start else 0.0
    rate_min = art / el_min if el_min > 0 else 0.0
    remain = expected - art
    eta_h = (remain / rate_min / 60.0) if rate_min > 0 else None

    def top(d: defaultdict[str, list[float]], n: int = 8):
        rows = [(k, len(v), round(sum(v) / len(v), 2)) for k, v in d.items()]
        rows.sort(key=lambda x: -x[1])
        return rows[:n]

    try:
        load = open("/proc/loadavg", encoding="utf-8").read().split()[:3]
    except OSError:
        load = []
    workers = int(subprocess.check_output(["pgrep", "-fc", "run_pipeline"], text=True).strip() or "0")
    tmux_ok = subprocess.run(["tmux", "has-session", "-t", "vbt_full"], capture_output=True).returncode == 0
    log_mtime = os.path.getmtime(log) if os.path.isfile(log) else None
    stall_min = (now.timestamp() - log_mtime) / 60.0 if log_mtime else None

    rec = {
        "ts_utc": now.isoformat(),
        "run_id": run_id,
        "tmux": tmux_ok,
        "ok_count": art,
        "expected": expected,
        "pct": round(100.0 * art / expected, 4) if expected else None,
        "workers": workers,
        "loadavg": load,
        "log_mtime_utc": datetime.fromtimestamp(log_mtime, tz=timezone.utc).isoformat() if log_mtime else None,
        "log_stall_min": round(stall_min, 2) if stall_min is not None else None,
        "rate_per_min_overall": round(rate_min, 2),
        "rate_per_hr_overall": round(rate_min * 60, 0),
        "eta_hours_overall": round(eta_h, 1) if eta_h else None,
        "fail_log": int(subprocess.check_output(f"grep -ci fail {orch} || true", shell=True, text=True).strip() or 0)
        if os.path.isfile(orch)
        else 0,
        "traceback_log": int(
            subprocess.check_output(f"grep -ci traceback {orch} || true", shell=True, text=True).strip() or 0
        )
        if os.path.isfile(orch)
        else 0,
        "top_models": top(md),
        "top_events": top(ed),
    }
    anomalies = []
    if not tmux_ok and art < expected:
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

    with open(monitor, "a", encoding="utf-8") as out:
        out.write(json.dumps(rec) + "\n")
    print(json.dumps(rec, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
