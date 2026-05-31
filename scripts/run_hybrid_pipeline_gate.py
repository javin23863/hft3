#!/usr/bin/env python3
"""
End-to-end PASS gate: PDF_MODEL_4 hybrid backtest on one real Databento MBO event.

Repeatable workstation process (not a one-off snapshot):
  1. Preflight — events.csv + NPZ present
  2. Unit tests — pdf hybrid strategy + ablation toggles
  3. Defensive ablation — 4 PDF_MODEL_4 modes (optional)
  4. Backtest — PDF_MODEL_1→3→4 hybrid_full via ReplayRunner
  5. After-action — LLM narrative on hybrid artifact dir (optional)
  6. Validate — research card + min-trades
  7. Manifest — runtime/reports/hybrid_pipeline_gate.json (PASS/FAIL)

Exit 0 only when all steps PASS.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

DEFAULT_EVENT = "CPI_2024_09_11_TIGHT"
DEFAULT_SYMBOL = "MES.v.0"
GATE_REPORT = _REPO / "runtime" / "reports" / "hybrid_pipeline_gate.json"
CARD_DIR = _REPO / "research_cards" / "PDF_MODEL_4_hybrid_replay"
ABLATION_DIR = _REPO / "research_cards" / "PDF_MODEL_4_defensive_ablation"
HYBRID_AAR_DIR = _REPO / "research_cards" / "PDF_MODEL_4_hybrid_pipeline"


def _step(name: str, ok: bool, detail: str = "") -> Dict[str, Any]:
    return {"step": name, "status": "PASS" if ok else "FAIL", "detail": detail}


def _after_action_allowed() -> bool:
    import os

    if os.environ.get("HFT3_AFTER_ACTION") == "0":
        return False
    if os.environ.get("HFT3_AFTER_ACTION") == "1":
        return True
    return sys.platform in ("win32", "darwin")


def preflight(event_id: str, symbol: str) -> Tuple[bool, str, Path]:
    from backtest.adapters.rithmic_replay_loader import resolve_event_npz

    csv_path = _REPO / "data_system" / "config" / "events.csv"
    if not csv_path.is_file():
        return False, f"missing {csv_path}", Path()
    try:
        npz = resolve_event_npz(event_id, _REPO, symbol=symbol)
    except FileNotFoundError as exc:
        return False, str(exc), Path()
    if not npz.is_file():
        return False, f"NPZ not on disk: {npz}", npz
    return True, str(npz), npz


def run_unit_tests() -> Tuple[bool, str]:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/backtest_pipeline/test_pdf_hybrid_strategy.py",
        "tests/backtest_pipeline/test_pdf_defensive_ablation.py",
        "-q",
        "--tb=line",
    ]
    proc = subprocess.run(cmd, cwd=str(_REPO), capture_output=True, text=True)
    tail = (proc.stdout or "") + (proc.stderr or "")
    summary = tail.strip().splitlines()[-1] if tail.strip() else f"exit {proc.returncode}"
    return proc.returncode == 0, summary


def resolve_latency(latency_ms: float | None) -> Tuple[float, str, str]:
    from backtest_pipeline.src.chi404_latency import (
        BACKTEST_LATENCY_NOTE_MEASURED,
        BACKTEST_LATENCY_NOTE_UNMEASURED,
        DEFAULT_CHI404_SUMMARY,
        resolve_replay_latency_ms,
    )

    if latency_ms is not None:
        ms, src, _ = resolve_replay_latency_ms(latency_ms=latency_ms)
        note = (
            f"{BACKTEST_LATENCY_NOTE_UNMEASURED} "
            f"Gate used CLI --latency-ms {ms} for workstation replay (not CHI404 measured)."
        )
        return ms, src, note

    try:
        ms, src, _ = resolve_replay_latency_ms(
            latency_ms=None, chi404_summary=DEFAULT_CHI404_SUMMARY
        )
        return ms, src, BACKTEST_LATENCY_NOTE_MEASURED
    except ValueError:
        ms, src, _ = resolve_replay_latency_ms(latency_ms=1.0)
        note = (
            f"{BACKTEST_LATENCY_NOTE_UNMEASURED} "
            "Gate used --latency-ms 1.0 workstation fallback for repeatable local PASS."
        )
        return ms, "workstation_gate_fallback_1ms", note


def _load_pdf_hybrid_replay_module():
    script = _REPO / "scripts" / "run_pdf_hybrid_replay.py"
    spec = importlib.util.spec_from_file_location("run_pdf_hybrid_replay", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _load_ablation_writer():
    script = _REPO / "scripts" / "run_pdf_hybrid_ablation.py"
    spec = importlib.util.spec_from_file_location("run_pdf_hybrid_ablation", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def run_defensive_ablation(
    event_id: str,
    npz_path: Path,
    latency_ms: float | None,
) -> Tuple[bool, str, Dict[str, Any]]:
    from backtest_pipeline.src.pdf_hybrid_ablation import run_defensive_ablation_matrix

    ms, src, _ = resolve_latency(latency_ms)
    mod = _load_pdf_hybrid_replay_module()
    event_meta = mod.load_event_row(event_id, _REPO / "data_system" / "config" / "events.csv")
    matrix = run_defensive_ablation_matrix(
        npz_path=npz_path,
        event_meta=event_meta,
        tick_size=0.25,
        latency_ms=ms,
        queue_model="LogProbQueueModel2",
        step_ns=100_000,
    )
    errors: List[str] = []
    summary_rows: List[Dict[str, Any]] = []
    for mode in matrix.get("modes", []):
        metrics = mode.get("metrics") or {}
        row = {
            "mode_id": mode.get("mode_id"),
            "use_ofi": mode.get("use_ofi"),
            "use_vpin": mode.get("use_vpin"),
            "num_trades": metrics.get("num_trades", 0),
            "net_pnl_after_fee": metrics.get("net_pnl_after_fee", metrics.get("net_pnl", 0)),
            "mean_vpin": metrics.get("mean_vpin"),
        }
        summary_rows.append(row)
        if "error" in metrics:
            errors.append(f"{mode.get('mode_id')}: {metrics['error']}")

    ablation_mod = _load_ablation_writer()
    ablation_mod.write_ablation_report(ABLATION_DIR, matrix, event_meta)

    if errors:
        return False, "; ".join(errors), {"modes": summary_rows, "matrix": matrix}
    detail = ", ".join(
        f"{r['mode_id']}: trades={r['num_trades']}" for r in summary_rows
    )
    return True, f"latency={ms}ms ({src}) {detail}", {"modes": summary_rows, "matrix": matrix}


def run_hybrid_backtest(
    event_id: str,
    npz_path: Path,
    latency_ms: float | None,
) -> Tuple[bool, str, Dict[str, Any]]:
    mod = _load_pdf_hybrid_replay_module()
    ms, src, note = resolve_latency(latency_ms)
    event_meta = mod.load_event_row(event_id, _REPO / "data_system" / "config" / "events.csv")
    payload = mod.run_pdf_hybrid_replay(
        event_id=event_id,
        npz_path=npz_path,
        event_meta=event_meta,
        tick_size=0.25,
        latency_ms=ms,
        latency_source=src,
        chi404_measured_speed=None,
        queue_model="LogProbQueueModel2",
        step_ns=100_000,
        use_ofi=True,
        use_vpin=True,
    )
    payload["gate_latency_note"] = note
    result = payload.get("result") or {}
    if "error" in result:
        return False, str(result["error"]), payload

    mod.write_research_card(CARD_DIR, payload, event_meta)
    detail = (
        f"steps={result.get('steps')} trades={result.get('num_trades')} "
        f"balance={result.get('balance')} latency={ms}ms ({src})"
    )
    return True, detail, payload


def run_after_action_step(
    event_id: str,
    symbol: str,
    hybrid_payload: Dict[str, Any],
    ablation_bundle: Dict[str, Any],
    latency_ms: float | None,
) -> Tuple[bool, str, Dict[str, Any]]:
    if not _after_action_allowed():
        return True, "skipped (after-action disabled on this host)", {"llm_status": "skipped_host"}

    from backtest_pipeline.src.pipeline_aar_artifacts import write_hybrid_aar_artifacts
    from data_layer.pipeline.after_action import run_after_action_report

    ms, src, _ = resolve_latency(latency_ms)
    matrix = ablation_bundle.get("matrix") if ablation_bundle else None
    write_hybrid_aar_artifacts(
        HYBRID_AAR_DIR,
        event_id=event_id,
        symbol=symbol,
        hybrid_payload=hybrid_payload,
        ablation_matrix=matrix,
        latency_ms=ms,
        latency_source=src,
    )
    try:
        meta = run_after_action_report(HYBRID_AAR_DIR, _REPO)
    except Exception as exc:
        logging.getLogger(__name__).warning("after-action report failed: %s", exc)
        return False, str(exc), {"llm_status": "failed", "after_action_failed": str(exc)}

    status = str(meta.get("llm_status", "unknown"))
    report_path = HYBRID_AAR_DIR / "after_action_report.md"
    if report_path.is_file():
        detail = f"llm_status={status} report written"
    else:
        detail = f"llm_status={status} (symbolic/packet only)"
    return True, detail, meta


def validate_card(min_trades: int = 0) -> Tuple[bool, str]:
    result_path = CARD_DIR / "result.json"
    report_path = CARD_DIR / "report.md"
    if not result_path.is_file() or not report_path.is_file():
        return False, "research card missing"
    card = json.loads(result_path.read_text(encoding="utf-8"))
    engine = (card.get("engines") or {}).get("pdf_hybrid") or {}
    res = engine.get("result") or {}
    if engine.get("model_id") != "PDF_MODEL_4":
        return False, "model_id not PDF_MODEL_4"
    if "error" in res:
        return False, str(res["error"])
    if int(res.get("steps") or 0) <= 0:
        return False, "steps <= 0"
    trades = int(res.get("num_trades") or 0)
    if trades < min_trades:
        return False, f"num_trades={trades} < min_trades={min_trades}"
    return True, f"card OK event_id={card.get('event_id')} trades={trades}"


def main() -> int:
    parser = argparse.ArgumentParser(description="PDF_MODEL_4 hybrid pipeline PASS gate")
    parser.add_argument("--event-id", default=DEFAULT_EVENT)
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument(
        "--latency-ms",
        type=float,
        default=None,
        help="Override replay latency (default: CHI404 measured, else 1.0ms gate fallback)",
    )
    parser.add_argument("--skip-unit-tests", action="store_true")
    parser.add_argument("--skip-ablation", action="store_true")
    parser.add_argument("--skip-after-action", action="store_true")
    parser.add_argument(
        "--min-trades",
        type=int,
        default=1,
        help="Require at least this many fills in hybrid_full backtest (default: 1)",
    )
    parser.add_argument(
        "--tier",
        choices=("hybrid", "smoke", "catalog"),
        default="hybrid",
        help="hybrid: PDF_MODEL_4 only (default); smoke/catalog: delegate to run_full_pipeline_gate.py",
    )
    args = parser.parse_args()

    if args.tier in ("smoke", "catalog"):
        import subprocess

        cmd = [
            sys.executable,
            str(_REPO / "scripts" / "run_full_pipeline_gate.py"),
            "--tier",
            args.tier,
            "--event-id",
            args.event_id,
            "--symbol",
            args.symbol,
            "--min-trades",
            str(args.min_trades),
        ]
        if args.skip_unit_tests:
            cmd.append("--skip-unit-tests")
        if args.skip_ablation:
            cmd.append("--skip-ablation")
        if args.skip_after_action:
            cmd.append("--skip-after-action")
        if args.latency_ms is not None:
            cmd.extend(["--latency-ms", str(args.latency_ms)])
        return subprocess.call(cmd)

    steps: List[Dict[str, Any]] = []
    overall = True

    ok, detail, npz = preflight(args.event_id, args.symbol)
    steps.append(_step("preflight", ok, detail))
    overall &= ok

    if ok and not args.skip_unit_tests:
        ok, detail = run_unit_tests()
        steps.append(_step("unit_tests", ok, detail))
        overall &= ok

    ablation_bundle: Dict[str, Any] = {}
    if ok and not args.skip_ablation:
        ok, detail, ablation_bundle = run_defensive_ablation(args.event_id, npz, args.latency_ms)
        steps.append(_step("defensive_ablation", ok, detail))
        overall &= ok

    payload: Dict[str, Any] = {}
    if ok:
        ok, detail, payload = run_hybrid_backtest(args.event_id, npz, args.latency_ms)
        steps.append(_step("hybrid_backtest", ok, detail))
        overall &= ok

    after_action_meta: Dict[str, Any] = {}
    if ok and not args.skip_after_action and payload:
        ok, detail, after_action_meta = run_after_action_step(
            args.event_id,
            args.symbol,
            payload,
            ablation_bundle,
            args.latency_ms,
        )
        steps.append(_step("after_action_report", ok, detail))
        overall &= ok

    if overall:
        ok, detail = validate_card(args.min_trades)
        steps.append(_step("validate_card", ok, detail))
        overall &= ok

    manifest = {
        "gate": "PDF_MODEL_4_hybrid_pipeline",
        "status": "PASS" if overall else "FAIL",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event_id": args.event_id,
        "symbol": args.symbol,
        "research_card": str(CARD_DIR.relative_to(_REPO)).replace("\\", "/"),
        "ablation_card": str(ABLATION_DIR.relative_to(_REPO)).replace("\\", "/"),
        "hybrid_aar_dir": str(HYBRID_AAR_DIR.relative_to(_REPO)).replace("\\", "/"),
        "steps": steps,
        "ablation_summary": ablation_bundle.get("modes"),
        "after_action": after_action_meta,
        "backtest_summary": {
            "model_id": "PDF_MODEL_4",
            "engine": "pdf_hybrid",
            "dependency_chain": ["PDF_MODEL_1", "PDF_MODEL_3", "PDF_MODEL_4"],
            "result": (payload.get("result") if payload else None),
            "latency_ms": payload.get("latency_ms") if payload else None,
            "latency_source": payload.get("latency_source") if payload else None,
            "gate_latency_note": payload.get("gate_latency_note") if payload else None,
        },
        "repeat_command": (
            f"python scripts/run_hybrid_pipeline_gate.py "
            f"--event-id {args.event_id} --symbol {args.symbol}"
        ),
    }
    GATE_REPORT.parent.mkdir(parents=True, exist_ok=True)
    GATE_REPORT.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(json.dumps(manifest, indent=2))
    print(f"Wrote {GATE_REPORT}")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
