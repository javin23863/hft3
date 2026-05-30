"""Workbench run orchestrator — C++ latency is source of truth."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from backtest.adapters.rithmic_replay_loader import resolve_event_npz
from backtest_pipeline.src.signal_backtester import BacktestResult
from workbench.src.core.trade_audit import audit_records_to_dataframe
from workbench.src.data.l3_loader import L3Loader
from workbench.src.data.manifest import DatasetManifest
from workbench.src.latency.viability import analyze_latency_viability, sweep_injection_pnl
from workbench.src.core.composition import ModelComposition
from workbench.src.registry.composition_orchestrator import CompositionOrchestrator
from workbench.src.registry.unified_registry import build_models_config, get_model_by_id
from workbench.src.report.generator import (
    generate_hyp_research_card,
    generate_pdf_research_card,
    render_markdown_report,
    write_run_report,
)
from workbench.src.robustness.pack import run_robustness_pack
from workbench.src.run.run_context import RunContext
from workbench.src.sim.cpp_latency_profile import CppLatencyProfile
from workbench.src.sim.cpp_stack_verify import get_cached_stack_verify
from workbench.src.sim.latency_simulator import LatencyPolicy


def _after_action_allowed() -> bool:
    """Post-run LLM runs on dev workstation only (BLUEPRINT live path is CHI404)."""
    import os
    import sys

    if os.environ.get("HFT3_AFTER_ACTION") == "0":
        return False
    if os.environ.get("HFT3_AFTER_ACTION") == "1":
        return True
    return sys.platform in ("win32", "darwin")


class WorkbenchEngine:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    def run(
        self,
        model_id: str,
        event_id: str,
        *,
        symbol: Optional[str] = None,
        npz_path: Optional[Path] = None,
        chi404_summary: Optional[Path] = None,
        seed: int = 42,
        history_years_available: float = 0.0,
        skip_history_gate: bool = True,
        fast_sweep: bool = True,
        composition: Optional[ModelComposition] = None,
        strategy_params: Optional[Dict[str, Any]] = None,
        wfc_status: Optional[str] = None,
    ) -> Dict[str, Any]:
        effective = composition or CompositionOrchestrator.default_composition(model_id)
        primary_id = effective.primary_model_id
        cfg = build_models_config()[primary_id]
        if npz_path is not None:
            resolved_npz = Path(npz_path)
            if not resolved_npz.is_file():
                raise FileNotFoundError(f"NPZ missing: {resolved_npz}")
        else:
            resolved_npz = resolve_event_npz(event_id, self.repo_root, symbol=symbol)
        loader = L3Loader(require_snapshot_on_gap=True)
        loader.mark_snapshot_available()
        raw = loader.load(str(resolved_npz))

        chi404 = chi404_summary or (self.repo_root / "runtime/latency_reports/latency_summary.json")
        if chi404.is_file():
            cpp_profile = CppLatencyProfile.from_chi404_summary(chi404)
            policy = LatencyPolicy.from_cpp_profile(cpp_profile)
            measured_ms = cpp_profile.measured_production_p99_ms
        else:
            cpp_profile = CppLatencyProfile.from_yaml_defaults()
            policy = LatencyPolicy.from_cpp_profile(cpp_profile)
            measured_ms = cpp_profile.measured_production_p99_ms

        manifest = DatasetManifest.from_loader(
            resolved_npz,
            event_id,
            loader.report,
            min_history_years=cfg.min_history_years,
            history_years_available=history_years_available,
            chi404_summary=json.loads(chi404.read_text(encoding="utf-8")) if chi404.is_file() else None,
        )
        if skip_history_gate:
            manifest.data_sufficient = True

        ctx = RunContext.build(
            self.repo_root,
            primary_id,
            event_id,
            resolved_npz,
            raw,
            seed=seed,
            chi404_summary=chi404 if chi404.is_file() else None,
            latency_policy=policy,
            measured_p99_ms=measured_ms,
        )
        ctx.metadata["data_sufficient"] = manifest.data_sufficient
        if strategy_params:
            ctx.metadata["strategy_params"] = dict(strategy_params)
        if effective.defensive_stubs:
            ctx.metadata["composition"] = effective.to_dict()
        ctx.write_reproducibility_files()
        manifest.write_json(ctx.artifact_dir / "manifest.json")

        if manifest.gate_error() and not skip_history_gate:
            raise RuntimeError(manifest.gate_error())

        model = get_model_by_id(primary_id)
        val_errs = model.validate_inputs(ctx)
        if val_errs and not skip_history_gate:
            raise ValueError("; ".join(val_errs))

        cpp_verify = get_cached_stack_verify(self.repo_root)

        comp_trace = None
        if effective.defensive_stubs:
            comp_orch = CompositionOrchestrator()
            result, comp_trace = comp_orch.run(ctx, effective)
            trace_path = ctx.artifact_dir / "composition_trace.json"
            trace_path.write_text(json.dumps(comp_trace.to_dict(), indent=2), encoding="utf-8")
        else:
            result = model.run_backtest(ctx)
        diagnostics = model.produce_diagnostics(ctx, result) if not fast_sweep else None
        from workbench.src.adapters.hypothesis_adapter import HypothesisAdapter

        if isinstance(model, HypothesisAdapter):
            model._build_audit(ctx, result)

        audit_records = ctx.metadata.get("audit_records", [])
        python_runtime_us = float(ctx.metadata.get("python_research_runtime_us", 0.0))

        if isinstance(result, BacktestResult):
            base = {"net_pnl": result.net_pnl, "expectancy": result.expectancy, "num_trades": result.num_trades}
        else:
            base = {"net_pnl": 0.0, "expectancy": 0.0, "num_trades": 0}

        def _run_at_latency(lat_ms: float) -> Dict[str, float]:
            m = get_model_by_id(model_id)
            inj_us = max(0.0, lat_ms * 1000.0 - cpp_profile.measured_production_p99_us)
            sub_policy = LatencyPolicy.from_cpp_profile(cpp_profile, injection_us=inj_us)
            sub = RunContext.build(
                self.repo_root, model_id, event_id, resolved_npz, raw,
                seed=seed, measured_p99_ms=lat_ms,
                latency_policy=sub_policy,
                chi404_summary=chi404 if chi404.is_file() else None,
            )
            sub.metadata["data_sufficient"] = True
            r = m.run_backtest(sub)
            if isinstance(r, BacktestResult):
                return {"net_pnl": r.net_pnl, "expectancy": r.expectancy, "num_trades": r.num_trades}
            return {"net_pnl": 0.0, "expectancy": 0.0, "num_trades": 0}

        pnl_by_injection = sweep_injection_pnl(
            base["net_pnl"],
            cpp_profile,
            full_sweep=not fast_sweep,
            run_fn=_run_at_latency if not fast_sweep else None,
        )

        viability = analyze_latency_viability(
            base["net_pnl"],
            cpp_profile,
            cfg.latency_lane,
            pnl_by_injection_us=pnl_by_injection,
            audit_records=audit_records,
            python_research_runtime_us=python_runtime_us,
        )

        trade_pnls = [result.expectancy] * max(result.num_trades, 1) if isinstance(result, BacktestResult) else []
        robustness = run_robustness_pack(lambda: base, trade_pnls, sweep_count=1)

        net_pnl = base["net_pnl"]
        num_trades = base["num_trades"]
        expectancy = base["expectancy"]
        win_rate = getattr(result, "win_rate", 0.0) if isinstance(result, BacktestResult) else 0.0

        promote = (
            viability.survives_cpp_execution_delay
            and viability.simulated_latency_adjusted_pnl > 0
            and robustness.passed
            and (wfc_status is None or wfc_status in ("PASS", "SKIPPED"))
        )

        report = {
            "model_id": primary_id,
            "event_id": event_id,
            "data_period": event_id,
            "robustness_window": cfg.robustness_window,
            "latency_authority": "cpp_measured",
            "python_research_runtime_us": python_runtime_us,
            "cpp_hot_path_runtime_us": viability.cpp_hot_path_runtime_us,
            "measured_production_p99_us": viability.measured_production_p99_us,
            "measured_p99_ms": viability.measured_production_p99_ms,
            "breakeven_us": viability.breakeven_us,
            "breakeven_ms": viability.breakeven_ms,
            "latency_profitability_buffer_us": viability.latency_profitability_buffer_us,
            "latency_buffer_ms": viability.latency_buffer_ms,
            "simulated_latency_adjusted_pnl": viability.simulated_latency_adjusted_pnl,
            "survives_cpp_execution_delay": viability.survives_cpp_execution_delay,
            "lane_required": viability.lane_required,
            "lane_measured": viability.lane_measured,
            "lane_pass": viability.lane_pass,
            "overfit_risk": robustness.overfit_risk,
            "recommendation": viability.recommendation,
            "robustness_passed": robustness.passed,
            "net_pnl": net_pnl,
            "num_trades": num_trades,
            "promote_candidate": promote,
            "wfc_status": wfc_status,
            "pnl_by_injection_us": {str(k): v for k, v in pnl_by_injection.items()},
            "pnl_by_latency": viability.pnl_by_latency,
            "cpp_latency_profile": viability.cpp_latency_profile,
            "cpp_replay_available": False,
            "cpp_stack_verified": cpp_verify.stack_verified,
            "cpp_stack_checks": cpp_verify.checks,
            "cpp_stack_verify_reason": cpp_verify.reason,
        }
        if comp_trace is not None:
            report["composition"] = effective.to_dict()
            report["trades_vetoed_by_defense"] = comp_trace.trades_vetoed
            report["signal_raw"] = comp_trace.signal_raw
            report["signal_adjusted"] = comp_trace.signal_adjusted
            report["phase_budgets_us"] = comp_trace.phase_budgets_us

        md = render_markdown_report(primary_id, event_id, event_id, viability, robustness)
        write_run_report(ctx.artifact_dir, report, md)

        if audit_records:
            audit_records_to_dataframe(audit_records).to_parquet(
                ctx.artifact_dir / "trades.parquet", index=False
            )

        (ctx.artifact_dir / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "model_id": primary_id,
                    "event_id": event_id,
                    "seed": seed,
                    "latency_authority": "cpp_measured",
                    "cpp_latency_profile": cpp_profile.to_report_dict(),
                    "composition": effective.to_dict() if effective.defensive_stubs else None,
                }
            ),
            encoding="utf-8",
        )

        if cfg.kind == "hypothesis" and cfg.hyp_id:
            card = generate_hyp_research_card(
                cfg.hyp_id,
                {
                    "net_pnl": net_pnl,
                    "num_trades": num_trades,
                    "expectancy": expectancy,
                    "win_rate": win_rate,
                    "latency_bands": list(viability.pnl_by_latency.keys()),
                },
            )
        else:
            card = generate_pdf_research_card(
                primary_id,
                {**report, "name": cfg.name, "approval_status": "PASS" if promote else "FAIL"},
            )
        (ctx.artifact_dir / "research_card.json").write_text(json.dumps(card, indent=2), encoding="utf-8")

        if not fast_sweep and _after_action_allowed():
            after_action_meta: Dict[str, Any] = {}
            try:
                from data_layer.pipeline.after_action import run_after_action_report

                after_action_meta = run_after_action_report(ctx.artifact_dir, repo_root=self.repo_root)
            except Exception as exc:
                import logging

                logging.getLogger(__name__).warning("after-action report failed: %s", exc)
                meta_path = ctx.artifact_dir / "after_action_meta.json"
                if meta_path.is_file():
                    after_action_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                else:
                    after_action_meta = {"llm_status": "failed", "after_action_failed": str(exc)}

        return {
            "run_id": ctx.run_id,
            "artifact_dir": str(ctx.artifact_dir),
            "report": report,
            "diagnostics": diagnostics.metrics if diagnostics else {},
            "promote_candidate": promote,
            **(
                {"after_action": after_action_meta}
                if not fast_sweep and _after_action_allowed()
                else {}
            ),
        }
