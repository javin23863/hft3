#!/usr/bin/env python3
"""Full 55-model catalog gate — smoke (~15 min) or catalog (~2–4 hr) tier."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from backtest.adapters.rithmic_replay_loader import resolve_event_npz
from backtest_pipeline.src.pipeline_gate_report import finalize_catalog_models, write_catalog_artifacts
from backtest_pipeline.src.pipeline_hyp_fanout import fan_out_hyp_reports
from backtest_pipeline.src.pipeline_model_router import (
    PDF_DIAGNOSTICS,
    PDF_HYBRID_REPLAY,
    PDF_OPTIONS_FIXTURE,
    PDF_STRUCTURAL_EVAL,
    SMOKE_HYP_SAMPLE,
    route,
)
from features_engine.src.features.npz_feed import load_npz_events

DEFAULT_EVENT = "CPI_2024_09_11_TIGHT"
DEFAULT_SYMBOL = "MES.v.0"
PIPELINE_RUNS = _REPO / "research_cards" / "pipeline_runs"


def _step(name: str, ok: bool, detail: str) -> Dict[str, Any]:
    return {"step": name, "status": "PASS" if ok else "FAIL", "detail": detail}


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_REPO.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def _resolve_npz(event_id: str, symbol: str) -> Path:
    return resolve_event_npz(event_id, _REPO)


def _load_hybrid_gate():
    import importlib.util

    script = _REPO / "scripts" / "run_hybrid_pipeline_gate.py"
    spec = importlib.util.spec_from_file_location("run_hybrid_pipeline_gate", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _resolve_latency(latency_ms: float | None) -> Tuple[float, str]:
    hg = _load_hybrid_gate()
    ms, src, _ = hg.resolve_latency(latency_ms)
    return ms, src
    event_id: str,
    npz: Path,
    symbol: str,
    latency_ms: float | None,
    *,
    skip_ablation: bool,
    skip_after_action: bool,
    min_trades: int,
) -> Tuple[bool, str, Dict[str, Any]]:
    from scripts import run_hybrid_pipeline_gate as hg

    ablation_bundle: Dict[str, Any] = {}
    if not skip_ablation:
        ok, detail, ablation_bundle = hg.run_defensive_ablation(event_id, npz, latency_ms)
        if not ok:
            return False, detail, {}
    ok, detail, payload = hg.run_hybrid_backtest(event_id, npz, latency_ms)
    if not ok:
        return False, detail, {}
    res = payload.get("result") or {}
    trades = int(res.get("num_trades") or 0)
    if trades < min_trades:
        return False, f"hybrid num_trades={trades} < min_trades={min_trades}", payload
    if not skip_after_action:
        hg.run_after_action_step(event_id, symbol, payload, ablation_bundle, latency_ms)
    rt = route("PDF_MODEL_4")
    row = {
        "model_id": "PDF_MODEL_4",
        "engine_kind": rt.engine_kind,
        "status": "PASS",
        "artifact_dir": _relative(hg.CARD_DIR),
        "num_trades": trades,
        "net_pnl_usd": float(res.get("balance") or 0.0),
        "backend_label": rt.backend_label,
    }
    return True, detail, {"hybrid_row": row, "payload": payload}


def _run_hyp_batch(
    event_id: str,
    npz: Path,
    latency_ms: float | None,
    tier: str,
) -> Tuple[bool, str, List[Dict[str, Any]]]:
    from scripts.run_event_replay import run_event_accurate_mbo

    ms, _ = _resolve_latency(latency_ms)
    raw = load_npz_events(str(npz))
    if len(raw) == 0:
        return False, f"NPZ empty: {npz}", []
    mbo_result = run_event_accurate_mbo(raw, ms)
    only = SMOKE_HYP_SAMPLE if tier == "smoke" else None
    rows = fan_out_hyp_reports(
        mbo_result,
        event_id,
        PIPELINE_RUNS,
        repo_root=_REPO,
        only_model_ids=only,
        latency_ms=ms,
    )
    detail = f"MBO pass trades_total={mbo_result.get('total_trades_all_hypotheses')} fan_out={len(rows)}"
    return True, detail, rows


def _write_pdf_artifact(
    artifact_dir: Path,
    *,
    model_id: str,
    event_id: str,
    engine_kind: str,
    backend_label: str,
    result: Dict[str, Any],
    status: str,
) -> Dict[str, Any]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "model_id": model_id,
        "event_id": event_id,
        "engine_kind": engine_kind,
        "backend_label": backend_label,
        "status": status,
        "result": result,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    (artifact_dir / "result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (artifact_dir / "report.md").write_text(
        f"# {model_id}\n\n**Backend:** {backend_label}\n\n```json\n{json.dumps(result, indent=2)}\n```\n",
        encoding="utf-8",
    )
    num_trades = int(result.get("num_trades") or 0)
    net_pnl = float(result.get("net_pnl_usd") or result.get("net_pnl") or 0.0)
    return {
        "model_id": model_id,
        "engine_kind": engine_kind,
        "status": status,
        "artifact_dir": _relative(artifact_dir),
        "num_trades": num_trades,
        "net_pnl_usd": net_pnl,
        "backend_label": backend_label,
    }


def _run_pdf_structural_eval(
    model_id: str,
    event_id: str,
    npz: Path,
    latency_ms: float | None,
) -> Dict[str, Any]:
    from workbench.src.registry.unified_registry import get_model_by_id
    from workbench.src.run.run_context import RunContext

    ms, _ = _resolve_latency(latency_ms)
    raw = load_npz_events(str(npz))
    ctx = RunContext.build(
        _REPO,
        model_id,
        event_id,
        npz,
        raw,
        measured_p99_ms=ms,
    )
    adapter = get_model_by_id(model_id)
    errs = adapter.validate_inputs(ctx)
    artifact_dir = PIPELINE_RUNS / f"{model_id}_{event_id}"
    rt = route(model_id)
    if errs:
        return _write_pdf_artifact(
            artifact_dir,
            model_id=model_id,
            event_id=event_id,
            engine_kind=rt.engine_kind,
            backend_label=rt.backend_label,
            result={"validation_errors": errs},
            status="FAIL",
        )
    try:
        adapter.build_features(ctx)
        bt_res = adapter.run_backtest(ctx)
        if isinstance(bt_res, dict):
            result = {
                "signal": bt_res.get("signal"),
                "num_trades": 0,
                "net_pnl_usd": 0.0,
                "diagnostics_only": True,
            }
        else:
            result = {
                "num_trades": int(getattr(bt_res, "num_trades", 0)),
                "net_pnl_usd": float(getattr(bt_res, "net_pnl", 0.0)),
                "expectancy_usd": float(getattr(bt_res, "expectancy", 0.0)),
            }
        return _write_pdf_artifact(
            artifact_dir,
            model_id=model_id,
            event_id=event_id,
            engine_kind=rt.engine_kind,
            backend_label=rt.backend_label,
            result=result,
            status="PASS",
        )
    except Exception as exc:
        logging.getLogger(__name__).warning("pdf eval %s failed: %s", model_id, exc)
        return _write_pdf_artifact(
            artifact_dir,
            model_id=model_id,
            event_id=event_id,
            engine_kind=rt.engine_kind,
            backend_label=rt.backend_label,
            result={"error": str(exc)},
            status="FAIL",
        )


def _run_pdf_diagnostics(model_id: str, event_id: str, npz: Path, latency_ms: float | None) -> Dict[str, Any]:
    row = _run_pdf_structural_eval(model_id, event_id, npz, latency_ms)
    row["engine_kind"] = "pdf_diagnostics"
    row["backend_label"] = route(model_id).backend_label
    return row


def _run_pdf_options(event_id: str) -> Dict[str, Any]:
    from workbench.src.registry.unified_registry import get_model_by_id
    from workbench.src.run.run_context import RunContext

    model_id = "PDF_MODEL_5"
    rt = route(model_id)
    fixture = _REPO / "options_lane" / "fixtures" / "fair_futures_quotes.ndjson"
    artifact_dir = PIPELINE_RUNS / f"{model_id}_{event_id}"
    if not fixture.is_file():
        return {
            "model_id": model_id,
            "engine_kind": rt.engine_kind,
            "status": "SKIP_NO_FIXTURE",
            "artifact_dir": None,
            "num_trades": None,
            "net_pnl_usd": None,
            "backend_label": rt.backend_label,
            "reason": f"fixture missing: {fixture}",
        }
    import numpy as np

    adapter = get_model_by_id(model_id)
    ctx = RunContext(
        repo_root=_REPO,
        run_id=f"{model_id}_{event_id}",
        model_id=model_id,
        event_id=event_id,
        npz_path=fixture,
        events=np.array([]),
    )
    errs = adapter.validate_inputs(ctx)
    if errs:
        return {
            "model_id": model_id,
            "engine_kind": rt.engine_kind,
            "status": "FAIL",
            "artifact_dir": None,
            "num_trades": 0,
            "net_pnl_usd": 0.0,
            "backend_label": rt.backend_label,
            "reason": "; ".join(errs),
        }
    try:
        result = adapter.run_backtest(ctx)
        net = float(getattr(result, "net_pnl", 0.0))
        ntr = int(getattr(result, "num_trades", 0))
        return _write_pdf_artifact(
            artifact_dir,
            model_id=model_id,
            event_id=event_id,
            engine_kind=rt.engine_kind,
            backend_label=rt.backend_label,
            result={"net_pnl_usd": net, "num_trades": ntr},
            status="PASS" if ntr >= 0 else "FAIL",
        )
    except Exception as exc:
        return _write_pdf_artifact(
            artifact_dir,
            model_id=model_id,
            event_id=event_id,
            engine_kind=rt.engine_kind,
            backend_label=rt.backend_label,
            result={"error": str(exc)},
            status="FAIL",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Full 55-model catalog pipeline gate")
    parser.add_argument("--tier", choices=("smoke", "catalog"), default="smoke")
    parser.add_argument("--event-id", default=DEFAULT_EVENT)
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--latency-ms", type=float, default=None)
    parser.add_argument("--skip-hybrid", action="store_true")
    parser.add_argument("--skip-unit-tests", action="store_true")
    parser.add_argument("--skip-ablation", action="store_true", help="Skip 4-mode ablation in hybrid block")
    parser.add_argument("--skip-after-action", action="store_true")
    parser.add_argument("--min-trades", type=int, default=1)
    args = parser.parse_args()

    steps: List[Dict[str, Any]] = []
    executed: List[Dict[str, Any]] = []
    overall = True

    try:
        npz = _resolve_npz(args.event_id, args.symbol)
    except FileNotFoundError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2))
        return 1
    if not npz.is_file():
        print(json.dumps({"status": "FAIL", "error": f"NPZ missing: {npz}"}, indent=2))
        return 1

    steps.append(_step("preflight", True, f"npz={_relative(npz)}"))

    if not args.skip_unit_tests:
        hg = _load_hybrid_gate()
        ok, detail = hg.run_unit_tests()
        steps.append(_step("unit_tests", ok, detail))
        overall &= ok

    if overall and not args.skip_hybrid:
        ok, detail, bundle = _run_hybrid_block(
            args.event_id,
            npz,
            args.symbol,
            args.latency_ms,
            skip_ablation=args.skip_ablation or args.tier == "smoke",
            skip_after_action=args.skip_after_action or args.tier == "smoke",
            min_trades=args.min_trades,
        )
        steps.append(_step("hybrid_block", ok, detail))
        overall &= ok
        if bundle.get("hybrid_row"):
            executed.append(bundle["hybrid_row"])

    if overall:
        ok, detail, hyp_rows = _run_hyp_batch(args.event_id, npz, args.latency_ms, args.tier)
        steps.append(_step("hyp_batch_fanout", ok, detail))
        overall &= ok
        executed.extend(hyp_rows)

    if overall and args.tier == "catalog":
        for model_id in sorted(PDF_STRUCTURAL_EVAL):
            row = _run_pdf_structural_eval(model_id, args.event_id, npz, args.latency_ms)
            executed.append(row)
        for model_id in sorted(PDF_DIAGNOSTICS):
            row = _run_pdf_diagnostics(model_id, args.event_id, npz, args.latency_ms)
            executed.append(row)
        executed.append(_run_pdf_options(args.event_id))
        steps.append(_step("pdf_catalog_loop", True, f"structural={len(PDF_STRUCTURAL_EVAL)} diagnostics={len(PDF_DIAGNOSTICS)}"))

    models = finalize_catalog_models(executed, args.tier)
    assert len(models) == 55, f"expected 55 models, got {len(models)}"

    gate_path = write_catalog_artifacts(
        _REPO,
        tier=args.tier,
        event_id=args.event_id,
        symbol=args.symbol,
        models=models,
        steps=steps,
        overall_pass=overall,
    )

    manifest = json.loads(gate_path.read_text(encoding="utf-8"))
    print(json.dumps(manifest, indent=2))
    print(f"Wrote {gate_path}")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
