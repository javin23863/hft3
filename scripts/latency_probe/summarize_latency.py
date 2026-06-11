#!/usr/bin/env python3
"""Summarize CHI404 colo latency probe raw artifacts into JSON + markdown reports."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROBE_DIR = Path(__file__).resolve().parent
if str(_PROBE_DIR) not in sys.path:
    sys.path.insert(0, str(_PROBE_DIR))
from trial_profile import latest_latency_profile
from report_format import build_report_card, render_console, render_markdown
from native_probe_orders import collect_native_probe_orders

PASS_CRITERIA_REL = Path("infrastructure") / "chi404" / "PASS_CRITERIA.json"
REPORT_ROOT_REL = Path("runtime") / "latency_reports"

LANE_THRESHOLDS_MS = {
    1: (0.0, 2.0),
    2: (2.0, 10.0),
    3: (10.0, 50.0),
    4: (50.0, float("inf")),
}
PAPER_ORDER_MIN_PAIRED = 1000
ORDER_ACK_BLOCKED_NOTE = (
    "Paper order submit→ack not measured; run bash scripts/chi404_run_paper_latency_sweep.sh "
    "(native C++ rithmic_latency_probe campaign) on CHI404"
)
TRIAL_APPENDIX_LIMITATIONS = [
    "R|Trader VM log bridge; monotonic timestamps at ingest",
    "Promoted to authoritative when paired_count>=1000",
]
TRIAL_CONNECTORS = frozenset({"rtrader", "rtrader_bridge"})


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return {"error": f"invalid or unreadable: {path}"}
    return data if isinstance(data, dict) else {"error": "expected object"}


def _collect_cyclictest(raw_dir: Path) -> dict[str, Any]:
    runs: dict[str, dict[str, Any]] = {}
    for pct_path in sorted(raw_dir.glob("cyclictest_*/percentiles.json")):
        key = pct_path.parent.name
        body = _load_json(pct_path)
        runs[key] = body or {"error": "missing percentiles"}
    loaded_p99_vals: list[int] = []
    for key, body in runs.items():
        if not key.startswith("cyclictest_loaded_"):
            continue
        p99 = body.get("p99_us") if isinstance(body, dict) else None
        if isinstance(p99, int):
            loaded_p99_vals.append(p99)
        elif isinstance(p99, float):
            loaded_p99_vals.append(int(p99))
    max_p99 = max(loaded_p99_vals) if loaded_p99_vals else None
    return {
        "by_run": runs,
        "max_p99_us": max_p99,
        "gate_mode": "loaded_only",
        "loaded_runs": len(loaded_p99_vals),
    }


def network_p99_us(network: dict[str, Any] | None) -> tuple[float | None, str | None]:
    """Worst-case p99 across active colo network probes (microseconds)."""
    if not network:
        return None, None
    candidates: list[tuple[float, str]] = []
    for key, field in (
        ("rithmic_tcp_65000", "p99_ms"),
        ("rithmic_ping", "p99_ms"),
        ("gateway_ping", "p99_ms"),
        ("gateway_tcp_443", "p99_ms"),
    ):
        block = network.get(key)
        if not isinstance(block, dict) or block.get("status") not in (None, "ok"):
            continue
        val = block.get(field)
        if isinstance(val, (int, float)):
            candidates.append((float(val) * 1000.0, key))
    if not candidates:
        return None, None
    worst = max(candidates, key=lambda x: x[0])
    return worst[0], worst[1]


def _to_us(val: float, unit: str) -> float:
    u = unit.lower()
    if u.startswith("ms"):
        return val * 1000.0
    if u.startswith("s"):
        return val * 1_000_000.0
    return val


def _collect_clock_discipline(crit: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"status": "ok"}
    try:
        tracking = subprocess.check_output(
            ["chronyc", "tracking"],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=10,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"status": "error", "error": str(exc)}

    try:
        sources = subprocess.check_output(
            ["chronyc", "sources", "-v"],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=10,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        sources = f"(sources failed: {exc})"

    out["tracking_text"] = tracking.strip()
    out["sources_text"] = sources.strip()[:8000]

    parsed: dict[str, Any] = {}
    if "Leap status     : Not synchronised" in tracking:
        parsed["synchronised"] = False
    else:
        parsed["synchronised"] = "Not synchronised" not in tracking

    lo = re.search(r"Last offset\s*:\s*([+-]?[\d.]+)\s*(\S+)", tracking)
    if lo:
        parsed["last_offset_us"] = abs(_to_us(float(lo.group(1)), lo.group(2)))

    rm = re.search(r"RMS offset\s*:\s*([\d.]+)\s*(\S+)", tracking)
    if rm:
        parsed["rms_offset_us"] = abs(_to_us(float(rm.group(1)), rm.group(2)))

    lim_last = crit.get("epsilon_last_offset_halt_us", crit.get("epsilon_halt_us"))
    lim_rms = crit.get("epsilon_rms_halt_us", crit.get("epsilon_halt_us", 100) * 10)
    parsed["limits_us"] = {"last_offset_halt": lim_last, "rms_halt": lim_rms}

    warnings: list[str] = []
    if parsed.get("synchronised") is False:
        warnings.append("chrony not synchronised")
    if isinstance(parsed.get("last_offset_us"), (int, float)) and parsed["last_offset_us"] > lim_last:
        warnings.append(f"last offset {parsed['last_offset_us']:.0f}us > {lim_last}us")
    if isinstance(parsed.get("rms_offset_us"), (int, float)) and parsed["rms_offset_us"] > lim_rms:
        warnings.append(f"RMS offset {parsed['rms_offset_us']:.0f}us > {lim_rms}us")
    parsed["warnings"] = warnings
    out["parsed"] = parsed
    if warnings:
        out["status"] = "warn"
    return out


def _lane_from_order_ack_ms(order_p99_ms: float | None) -> int | None:
    if order_p99_ms is None:
        return None
    for lane, (lo, hi) in LANE_THRESHOLDS_MS.items():
        if lo <= order_p99_ms < hi:
            return lane
    return 4


def _classify_lane(
    *,
    cyclictest_pass: bool,
    network_pass: bool,
    order_ack_p99_ms: float | None,
    order_ack_blocked: bool,
    network_limit_us: int,
    cyclictest_limit_us: int,
) -> dict[str, Any]:
    infra_lane1 = cyclictest_pass and network_pass
    order_lane = _lane_from_order_ack_ms(order_ack_p99_ms)

    if order_ack_blocked or order_lane is None:
        lane = 1 if infra_lane1 else None
        partial = True
        reason = ORDER_ACK_BLOCKED_NOTE
    else:
        partial = False
        reason = None
        if order_lane == 1 and infra_lane1:
            lane = 1
        elif order_lane == 1 and not infra_lane1:
            lane = None
            partial = True
            reason = "order ack meets lane 1 but CPU/network do not"
        else:
            lane = order_lane

    lane_names = {
        1: "lane_1_hft_colo",
        2: "lane_2_low_latency",
        3: "lane_3_medium_latency",
        4: "lane_4_retail_remote",
    }
    return {
        "lane": lane,
        "lane_name": lane_names.get(lane) if lane else "undetermined",
        "partial": partial,
        "infrastructure_meets_lane_1": infra_lane1,
        "order_ack_lane": order_lane,
        "order_ack_blocked": order_ack_blocked,
        "note": reason,
        "criteria": {
            "lane_1": {
                "cyclictest_p99_max_us": cyclictest_limit_us,
                "network_p99_max_us": network_limit_us,
                "order_ack_p99_max_ms": 2.0,
            },
            "lane_2_order_ack_ms": "2-10",
            "lane_3_order_ack_ms": "10-50",
            "lane_4_order_ack_ms": "50+",
        },
    }


def _dominant_bottleneck(
    *,
    cyclictest_pass: bool,
    max_p99_us: int | None,
    network_pass: bool,
    network_p99_us_val: float | None,
    order_ack_blocked: bool,
    clock_warnings: list[str],
) -> str:
    if max_p99_us is not None and not cyclictest_pass:
        return f"kernel_scheduler_jitter (loaded cyclictest p99 {max_p99_us} us)"
    if network_p99_us_val is not None and not network_pass:
        return f"network_path (worst p99 {network_p99_us_val:.0f} us)"
    if clock_warnings:
        return f"clock_discipline ({clock_warnings[0]})"
    if order_ack_blocked:
        return "order_submit_to_ack_not_measured (R|Trader paper path)"
    return "none_identified"


def _strategy_guidance(
    lane_info: dict[str, Any],
    *,
    cyclictest_pass: bool,
    network_pass: bool,
) -> dict[str, list[str]]:
    partial = lane_info.get("partial", True)
    lane = lane_info.get("lane")
    realistic: list[str] = []
    unrealistic: list[str] = []

    if partial or lane is None:
        if cyclictest_pass and network_pass:
            realistic.append(
                "Backtest at 0.5-2 ms latency bands; colo CPU/network support HFT-tier infra."
            )
            unrealistic.append(
                "Assuming sub-2 ms order ack or production HFT execution without measured paper orders."
            )
        elif not cyclictest_pass:
            realistic.append("Fix kernel isolation / jitter before any sub-ms strategy research.")
            unrealistic.append("Sub-0.5 ms backtest bands while loaded cyclictest p99 fails PASS gate.")
        elif not network_pass:
            realistic.append("Diagnose gateway/Rithmic path before colo execution assumptions.")
            unrealistic.append("Tier-0.5 ms production tier with worst network p99 above PASS limit.")
        realistic.append(
            "See trial_order_ack_appendix for R|Trader VM order round-trip (non-production)."
        )
        return {"realistic": realistic, "unrealistic": unrealistic}

    if lane == 1:
        realistic.append("Production-style HFT: 0.5 ms bands, LogProbQueueModel2, CHI404-only hot path.")
        unrealistic.append("Workstation-mediated capture or orders (BLUEPRINT §4).")
    elif lane == 2:
        realistic.append("Low-latency scalping backtests at 2-10 ms bands.")
        unrealistic.append("Sub-2 ms order ack assumptions in live execution.")
    elif lane == 3:
        realistic.append("Medium-latency strategies; 10-50 ms backtest bands.")
        unrealistic.append("HFT queue models tuned for sub-ms ack.")
    else:
        realistic.append("Retail-remote research only; 50 ms+ dominant path.")
        unrealistic.append("Colo HFT or sub-10 ms production tiers.")

    return {"realistic": realistic, "unrealistic": unrealistic}


def _build_trial_order_ack_appendix(
    repo_root: Path,
    *,
    include: bool,
    disable_reason: str | None = None,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "authoritative": False,
        "source": "rithmic_trial_rtrader",
        "note": "Does not affect colo recommended_lane",
        "limitations": list(TRIAL_APPENDIX_LIMITATIONS),
    }
    if not include:
        return {
            **base,
            "status": "disabled",
            "reason": disable_reason or "trial appendix disabled",
        }

    profile = latest_latency_profile(repo_root)
    if profile is None:
        return {
            **base,
            "status": "missing",
            "reason": "no reports/rithmic_trial/.../latency_profile.json",
            "populate_hint": "Send paper orders in R|Trader VM, then bash scripts/chi404_run_trial_live.sh",
        }

    base["profile_path"] = profile.get("path")
    base["profile_date"] = profile.get("profile_date")
    base["connector"] = profile.get("connector")
    base["trusted"] = profile.get("trusted")

    if not profile.get("trusted"):
        return {
            **base,
            "status": "untrusted",
            "reason": "fixture or synthetic trial profile",
        }
    if profile.get("connector") not in TRIAL_CONNECTORS:
        return {
            **base,
            "status": "blocked",
            "reason": f"connector={profile.get('connector')!r}; need rtrader or rtrader_bridge",
        }

    order_stats = profile.get("order_submit_to_ack_us")
    if not isinstance(order_stats, dict) or not order_stats.get("count"):
        return {
            **base,
            "status": "missing",
            "reason": "No order ack events captured yet",
            "populate_hint": "Send paper orders in R|Trader VM, then bash scripts/chi404_run_trial_live.sh",
        }

    p99_us = order_stats.get("p99_us")
    if not isinstance(p99_us, (int, float)):
        return {
            **base,
            "status": "missing",
            "reason": "order_submit_to_ack_us.p99_us not available",
            "populate_hint": "Send paper orders in R|Trader VM, then bash scripts/chi404_run_trial_live.sh",
        }

    order_ack_p99_ms = float(p99_us) / 1000.0
    count = int(order_stats.get("count") or 0)
    authoritative = count >= PAPER_ORDER_MIN_PAIRED

    return {
        **base,
        "status": "ok",
        "order_submit_to_ack_us": order_stats,
        "order_ack_p99_ms": order_ack_p99_ms,
        "paired_count": count,
        "authoritative": authoritative,
        "note": (
            "Authoritative for backtest latency when paired_count>=1000"
            if authoritative
            else base["note"]
        ),
    }


def build_summary(
    repo_root: Path,
    run_id: str,
    *,
    include_trial_appendix: bool = True,
) -> dict[str, Any]:
    crit_path = repo_root / PASS_CRITERIA_REL
    crit = json.loads(crit_path.read_text(encoding="utf-8"))
    report_root = repo_root / REPORT_ROOT_REL
    raw_dir = report_root / "raw" / run_id

    cyclictest_limit_us = int(crit["cyclictest_p99_max_us"])
    network_limit_us = int(crit.get("network_p99_max_us", 500))

    cyclictest = _collect_cyclictest(raw_dir)
    max_p99 = cyclictest.get("max_p99_us")
    cyclictest_pass = (
        cyclictest.get("loaded_runs", 0) > 0
        and max_p99 is not None
        and max_p99 <= cyclictest_limit_us
    )

    loopback = _load_json(raw_dir / "loopback_tcp.json")
    network = _load_json(raw_dir / "network.json")
    network_p99_us_val, network_worst_source = network_p99_us(network)
    network_pass = network_p99_us_val is not None and network_p99_us_val <= network_limit_us

    clock = _collect_clock_discipline(crit)
    clock_warnings = (clock.get("parsed") or {}).get("warnings") or []

    trial_appendix = _build_trial_order_ack_appendix(
        repo_root,
        include=include_trial_appendix,
        disable_reason=(
            None
            if include_trial_appendix
            else "trial appendix disabled (--no-trial-appendix or LATENCY_PROBE_INCLUDE_TRIAL_APPENDIX=0)"
        ),
    )

    order_ack_blocked = True
    order_ack_p99_ms: float | None = None
    paper_order_latency: dict[str, Any] = {
        "measured": False,
        "authoritative": False,
        "paired_count": 0,
        "source": "rithmic_trial_rtrader",
        "measurement_tier": "log_bridge",
    }

    native_probe = collect_native_probe_orders(repo_root)
    native_count = native_probe.get("paired_count") or 0
    native_stats = native_probe.get("order_submit_to_ack_us") or {}
    native_p99_us = native_stats.get("p99_us")

    if native_count >= PAPER_ORDER_MIN_PAIRED and native_p99_us is not None and isinstance(native_p99_us, (int, float)):
        order_ack_p99_ms = float(native_p99_us) / 1000.0
        order_ack_blocked = False
        paper_order_latency = {
            "measured": True,
            "authoritative": True,
            "paired_count": native_count,
            "source": "rithmic_latency_probe_native_cpp",
            "measurement_tier": "native_cpp_probe",
            "hot_path_language": "c++",
            "wrapper": "none",
            "sample_files": native_probe.get("sample_files") or [],
            "runs": native_probe.get("runs") or [],
        }
    elif 0 < native_count < PAPER_ORDER_MIN_PAIRED:
        paper_order_latency = {
            "measured": False,
            "authoritative": False,
            "paired_count": native_count,
            "source": "rithmic_trial_rtrader",
            "measurement_tier": "log_bridge",
            "native_probe_partial": {
                "paired_count": native_count,
                "p99_us": native_p99_us,
            },
        }
    else:
        if trial_appendix.get("status") == "ok" and trial_appendix.get("authoritative"):
            stats = trial_appendix.get("order_submit_to_ack_us") or {}
            p99_us = stats.get("p99_us")
            count = int(trial_appendix.get("paired_count") or stats.get("count") or 0)
            if isinstance(p99_us, (int, float)):
                order_ack_p99_ms = float(p99_us) / 1000.0
                order_ack_blocked = False
                paper_order_latency = {
                    "measured": True,
                    "authoritative": True,
                    "paired_count": count,
                    "source": "rithmic_trial_rtrader",
                    "measurement_tier": "log_bridge",
                    "profile_path": trial_appendix.get("profile_path"),
                }

    if network and isinstance(network.get("rithmic_tcp_65000"), dict):
        network["rithmic_tcp_65000"]["network_health_only"] = True

    rithmic_tcp = (network or {}).get("rithmic_tcp_65000") or {}
    network_health = {
        "rithmic_tcp_65000_p99_ms": rithmic_tcp.get("p99_ms"),
        "network_health_only": True,
        "note": "TCP connect probes are network health telemetry; excluded from order_ack and backtest_latency_ms",
    }

    lane_info = _classify_lane(
        cyclictest_pass=cyclictest_pass,
        network_pass=network_pass,
        order_ack_p99_ms=order_ack_p99_ms,
        order_ack_blocked=order_ack_blocked,
        network_limit_us=network_limit_us,
        cyclictest_limit_us=cyclictest_limit_us,
    )

    summary: dict[str, Any] = {
        "run_id": run_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "authoritative_source": "chi404_bare_metal_only",
        "pass_criteria_path": str(crit_path.relative_to(repo_root)).replace("\\", "/"),
        "raw_dir": str(raw_dir.relative_to(repo_root)).replace("\\", "/"),
        "cyclictest": {
            **cyclictest,
            "limit_us": cyclictest_limit_us,
            "pass": cyclictest_pass,
        },
        "loopback_tcp": loopback,
        "network": network,
        "network_p99_us": network_p99_us_val,
        "network_p99_worst_source": network_worst_source,
        "network_limit_us": network_limit_us,
        "network_pass": network_pass,
        "clock_discipline": clock,
        "network_health": network_health,
        "rithmic_app_latency": {
            "status": "BLOCKED" if order_ack_blocked else "ok",
            "reason": ORDER_ACK_BLOCKED_NOTE if order_ack_blocked else "paper order submit→ack measured",
            "probe": "rithmic_gateway/tools/rithmic_latency_probe.cpp",
        },
        "e2e_harness": {
            "status": "BLOCKED" if order_ack_blocked else "ok",
            "reason": ORDER_ACK_BLOCKED_NOTE if order_ack_blocked else "paper waterfall available",
            "spec": "scripts/latency_probe/build_waterfall_report.py",
        },
        "native_probe_orders": native_probe,
        "trial_order_ack_appendix": trial_appendix,
        "paper_order_latency": paper_order_latency,
        "order_ack_p99_ms": order_ack_p99_ms,
        "order_ack_measured": not order_ack_blocked,
        "gates": {
            "cyclictest_pass": cyclictest_pass,
            "network_pass": network_pass,
            "order_ack_pass": (not order_ack_blocked) if include_trial_appendix else None,
        },
        "recommended_lane": lane_info,
        "dominant_bottleneck": _dominant_bottleneck(
            cyclictest_pass=cyclictest_pass,
            max_p99_us=max_p99,
            network_pass=network_pass,
            network_p99_us_val=network_p99_us_val,
            order_ack_blocked=order_ack_blocked,
            clock_warnings=clock_warnings,
        ),
        "strategy_guidance": _strategy_guidance(
            lane_info,
            cyclictest_pass=cyclictest_pass,
            network_pass=network_pass,
        ),
    }
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize CHI404 latency probe run.")
    parser.add_argument("--run-id", required=True, help="Probe run id (raw/ subdir name)")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repository root",
    )
    parser.add_argument(
        "--include-trial-appendix",
        action="store_true",
        default=False,
        help="Include R|Trader trial order-ack appendix (non-production)",
    )
    parser.add_argument(
        "--no-trial-appendix",
        action="store_true",
        help="Omit trial order-ack appendix",
    )
    args = parser.parse_args(argv)

    repo_root = args.repo.resolve()
    raw_dir = repo_root / REPORT_ROOT_REL / "raw" / args.run_id
    if not raw_dir.is_dir():
        print(f"raw dir missing: {raw_dir}", file=sys.stderr)
        return 2

    include_trial = args.include_trial_appendix and not args.no_trial_appendix
    summary = build_summary(repo_root, args.run_id, include_trial_appendix=include_trial)
    report_card = build_report_card(summary)
    summary["report_card"] = report_card

    report_root = repo_root / REPORT_ROOT_REL
    report_root.mkdir(parents=True, exist_ok=True)

    json_path = report_root / "latency_summary.json"
    md_path = report_root / "latency_summary.md"
    txt_path = report_root / "latency_report.txt"
    json_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(summary, report_card), encoding="utf-8")
    console_report = render_console(report_card)
    txt_path.write_text(console_report + "\n", encoding="utf-8")

    print(console_report)
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    print(f"Wrote {txt_path}")
    print(f"Dominant bottleneck: {summary['dominant_bottleneck']}")
    rec = summary["recommended_lane"]
    print(
        f"Recommended lane: {rec.get('lane')} ({rec.get('lane_name')})"
        + (" [partial]" if rec.get("partial") else "")
    )

    exit_code = 0
    gates = summary["gates"]
    if not gates["cyclictest_pass"]:
        print(
            f"cyclictest FAIL: loaded max p99 {summary['cyclictest'].get('max_p99_us')} us "
            f"> limit {summary['cyclictest'].get('limit_us')} us "
            f"(loaded_runs={summary['cyclictest'].get('loaded_runs')})",
            file=sys.stderr,
        )
        exit_code = 1
    if not gates["network_pass"]:
        print(
            f"network FAIL: worst p99 {summary.get('network_p99_us')} us "
            f"> limit {summary.get('network_limit_us')} us "
            f"(source={summary.get('network_p99_worst_source')})",
            file=sys.stderr,
        )
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
