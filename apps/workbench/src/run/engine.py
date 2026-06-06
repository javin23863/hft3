"""Workbench run orchestrator — C++ latency is source of truth."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import yaml

from backtest.adapters.rithmic_replay_loader import resolve_event_npz
from backtest_pipeline.src.signal_backtester import BacktestResult
from workbench.src.core.trade_audit import audit_records_to_dataframe, summarize_phase5_timestamp_schema
from workbench.src.data.coverage_check import compute_model_coverage
from workbench.src.data.l3_loader import L3Loader
from workbench.src.data.manifest import DatasetManifest
from workbench.src.latency.viability import analyze_latency_viability, sweep_injection_pnl
from workbench.src.latency.operating_envelope import (
    build_latency_operating_envelope,
    compact_envelope_fields,
    write_latency_operating_envelope,
)
from workbench.src.latency.execution_path_audit import load_current_low_latency_status
from workbench.src.core.composition import ModelComposition
from workbench.src.registry.composition_orchestrator import CompositionOrchestrator
from features_engine.src.model_registry import resolve_model_id
from workbench.src.registry.unified_registry import get_model_config, get_model_by_id
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
from hft3.validation.research_stamp import build_certification_stamp, format_stamp_footer


def _after_action_allowed() -> bool:
    """Post-run LLM runs on dev workstation only (BLUEPRINT live path is CHI404)."""
    import os
    import sys

    if os.environ.get("HFT3_AFTER_ACTION") == "0":
        return False
    if os.environ.get("HFT3_AFTER_ACTION") == "1":
        return True
    return sys.platform in ("win32", "darwin")


def _phase5_timestamp_schema_passes(schema: Mapping[str, Any]) -> bool:
    return bool(schema.get("complete") is True and schema.get("monotonic_non_decreasing") is True)


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
        coverage_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        effective = composition or CompositionOrchestrator.default_composition(model_id)
        primary_id = resolve_model_id(effective.primary_model_id)
        cfg = get_model_config(primary_id)
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
        chi404_observed = chi404.is_file()
        if chi404_observed:
            cpp_profile = CppLatencyProfile.from_chi404_summary(chi404)
            policy = LatencyPolicy.from_cpp_profile(cpp_profile)
            measured_ms = cpp_profile.measured_production_p99_ms
        else:
            cpp_profile = CppLatencyProfile.from_yaml_defaults()
            policy = LatencyPolicy.from_cpp_profile(cpp_profile)
            measured_ms = cpp_profile.measured_production_p99_ms

        if coverage_summary is None:
            coverage_symbol = symbol or "MES.v.0"
            coverage_summary = compute_model_coverage(self.repo_root, primary_id, coverage_symbol).to_dict()

        manifest = DatasetManifest.from_loader(
            resolved_npz,
            event_id,
            loader.report,
            min_history_years=cfg.min_history_years,
            history_years_available=history_years_available,
            chi404_summary=json.loads(chi404.read_text(encoding="utf-8")) if chi404.is_file() else None,
        )
        manifest.extra["coverage_summary"] = coverage_summary
        coverage_status = str(coverage_summary.get("coverage_status") or "")
        coverage_sufficient = manifest.data_sufficient
        if coverage_status:
            coverage_sufficient = coverage_status != "BELOW_MINIMUM"
        quality_sufficient = manifest.monotonic_violations == 0 and manifest.duplicate_order_ids == 0
        manifest.data_sufficient = coverage_sufficient and quality_sufficient
        data_gate_error = manifest.gate_error()

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
        ctx.metadata["history_gate_skipped"] = skip_history_gate
        ctx.metadata["data_gate_error"] = data_gate_error
        ctx.metadata["coverage_summary"] = coverage_summary
        if strategy_params:
            ctx.metadata["strategy_params"] = dict(strategy_params)
        if effective.defensive_stubs:
            ctx.metadata["composition"] = effective.to_dict()
        ctx.write_reproducibility_files()
        manifest.write_json(ctx.artifact_dir / "manifest.json")

        if data_gate_error and not skip_history_gate:
            raise RuntimeError(data_gate_error)

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
        if diagnostics is not None and diagnostics.metrics:
            (ctx.artifact_dir / "signal_diagnostics.json").write_text(
                json.dumps(diagnostics.metrics, indent=2), encoding="utf-8"
            )
        from workbench.src.adapters.hypothesis_adapter import HypothesisAdapter

        if isinstance(model, HypothesisAdapter):
            model._build_audit(ctx, result)

        audit_records = ctx.metadata.get("audit_records", [])
        python_runtime_us = float(ctx.metadata.get("python_research_runtime_us", 0.0))

        if isinstance(result, BacktestResult):
            base = {"net_pnl": result.net_pnl, "expectancy": result.expectancy, "num_trades": result.num_trades}
            expected_audit_count = max(int(result.num_trades), len(result.fills))
        else:
            base = {"net_pnl": 0.0, "expectancy": 0.0, "num_trades": 0}
            expected_audit_count = 0
        phase5_timestamp_schema = summarize_phase5_timestamp_schema(
            audit_records,
            expected_trade_count=expected_audit_count,
        )
        phase5_audit_passes = _phase5_timestamp_schema_passes(phase5_timestamp_schema)

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
            sub.metadata["data_sufficient"] = manifest.data_sufficient
            sub.metadata["history_gate_skipped"] = skip_history_gate
            sub.metadata["data_gate_error"] = data_gate_error
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

        execution_path_audit_status = load_current_low_latency_status(self.repo_root)
        trade_pnls = [result.expectancy] * max(result.num_trades, 1) if isinstance(result, BacktestResult) else []
        latency_envelope = build_latency_operating_envelope(
            run_id=ctx.run_id,
            model_id=primary_id,
            event_id=event_id,
            viability=viability,
            cpp_profile=cpp_profile,
            phase5_timestamp_schema=phase5_timestamp_schema,
            audit_records=audit_records,
            composition=effective,
            composition_trace=comp_trace,
            chi404_observed=chi404_observed,
            wfc_status=wfc_status,
            execution_path_audit_status=execution_path_audit_status,
        )
        latency_checks = latency_envelope.get("checks", {})

        def _latency_check_passed(name: str) -> bool:
            check = latency_checks.get(name)
            return bool(isinstance(check, dict) and check.get("passed") is True)

        robustness_metrics = {
            **base,
            "survives_cpp_execution_delay": viability.survives_cpp_execution_delay,
            "operating_envelope_generated": _latency_check_passed("operating_envelope_generated"),
            "low_latency_execution_path_audit_pass": _latency_check_passed("low_latency_execution_path_audit"),
            "placement_speed_sensitivity_pass": _latency_check_passed("placement_speed_sensitivity"),
            "async_ack_state_risk_pass": _latency_check_passed("async_ack_state_risk"),
            "pending_exposure_guardrails_pass": _latency_check_passed("pending_exposure_guardrails"),
            "composition_latency_feasibility_pass": _latency_check_passed("composition_latency_feasibility"),
            "competitor_speed_sensitivity_pass": _latency_check_passed("competitor_speed_sensitivity"),
        }
        robustness = run_robustness_pack(lambda: robustness_metrics, trade_pnls, sweep_count=1)
        latency_envelope = build_latency_operating_envelope(
            run_id=ctx.run_id,
            model_id=primary_id,
            event_id=event_id,
            viability=viability,
            cpp_profile=cpp_profile,
            phase5_timestamp_schema=phase5_timestamp_schema,
            audit_records=audit_records,
            composition=effective,
            composition_trace=comp_trace,
            chi404_observed=chi404_observed,
            wfc_status=wfc_status,
            robustness=robustness,
            execution_path_audit_status=execution_path_audit_status,
        )
        write_latency_operating_envelope(ctx.artifact_dir, latency_envelope)
        latency_envelope_compact = compact_envelope_fields(latency_envelope)
        latency_envelope_passes = latency_envelope.get("status") == "PASS"

        net_pnl = base["net_pnl"]
        num_trades = base["num_trades"]
        expectancy = base["expectancy"]
        win_rate = getattr(result, "win_rate", 0.0) if isinstance(result, BacktestResult) else 0.0

        promote = (
            viability.survives_cpp_execution_delay
            and viability.simulated_latency_adjusted_pnl > 0
            and robustness.passed
            and phase5_audit_passes
            and latency_envelope_passes
            and manifest.data_sufficient
            and (wfc_status is None or wfc_status in ("PASS", "SKIPPED"))
        )

        cert_stamp = build_certification_stamp(
            event_id=event_id,
            model_id=primary_id,
            instrument=cfg.symbol if hasattr(cfg, "symbol") else "",
            execution_mode="REPLAY",
            execution_adapter_mode="workbench_replay",
            latency_band=viability.measured_production_p99_ms,
            fee_model="FeeModel",
        )
        if cert_stamp.get("promotion_eligible") is False:
            promote = False

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
            "data_sufficient": manifest.data_sufficient,
            "history_gate_skipped": skip_history_gate,
            "data_gate_error": data_gate_error,
            "wfc_status": wfc_status,
            "certification_stamp": cert_stamp,
            "certification_footer": format_stamp_footer(cert_stamp),
            "pnl_by_injection_us": {str(k): v for k, v in pnl_by_injection.items()},
            "pnl_by_latency": viability.pnl_by_latency,
            "cpp_latency_profile": viability.cpp_latency_profile,
            "cpp_replay_available": False,
            "cpp_stack_verified": cpp_verify.stack_verified,
            "cpp_stack_checks": cpp_verify.checks,
            "cpp_stack_verify_reason": cpp_verify.reason,
            "phase5_timestamp_schema": phase5_timestamp_schema,
            "latency_operating_envelope_status": latency_envelope.get("status"),
            "latency_operating_envelope": latency_envelope_compact,
            "latency_operating_envelope_checks": latency_envelope.get("checks", {}),
            "latency_operating_envelope_blockers": latency_envelope.get("promotion_blockers", []),
            "coverage_summary": coverage_summary,
        }
        if comp_trace is not None:
            report["composition"] = effective.to_dict()
            report["trades_vetoed_by_defense"] = comp_trace.trades_vetoed
            report["signal_raw"] = comp_trace.signal_raw
            report["signal_adjusted"] = comp_trace.signal_adjusted
            report["phase_budgets_us"] = comp_trace.phase_budgets_us

        md = render_markdown_report(primary_id, event_id, event_id, viability, robustness)
        md = md + "\n\n_" + format_stamp_footer(cert_stamp) + "_\n"
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
                    "coverage_summary": coverage_summary,
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
            card["certification_stamp"] = cert_stamp
        else:
            card = generate_pdf_research_card(
                primary_id,
                {**report, "name": cfg.name, "approval_status": "PASS" if promote else "FAIL"},
            )
            card["certification_stamp"] = cert_stamp
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
