"""Phased defensive + primary signal composition orchestrator."""

from __future__ import annotations

import time
from dataclasses import fields
from typing import Any, List, Optional, Tuple

from backtest_pipeline.src.signal_backtester import BacktestResult

from workbench.src.core.composition import (
    CompositionTrace,
    CompositionTraceStep,
    DefensiveStub,
    ModelComposition,
    Phase,
)
from workbench.src.core.defensive import DefensiveDiagnostics, DefensiveModel, FilterAction, FilterDecision
from workbench.src.registry.model_catalog import get_catalog_entry, resolve_stub_dependencies, validate_composition
from workbench.src.registry.pdf_orchestrator import PdfOrchestrator
from workbench.src.registry.unified_registry import build_models_config, get_model_by_id
from workbench.src.run.run_context import RunContext

_PHASE_ORDER: tuple[Phase, ...] = ("continuous", "before", "during", "after")
_PDF_SIGNAL_PRIMARIES = frozenset(
    {"TRANSFER_ENTROPY", "STOCHASTIC_THERMO", "CROSS_ASSET_LEAD_LAG", "DOW_YM_INDEX"}
)


class _CatalogDefensiveShim(DefensiveModel):
    """Adapter that forces catalog stubs through the DefensiveModel contract."""

    def __init__(self, orchestrator: "CompositionOrchestrator", stub: DefensiveStub, entry: Any, phase: Phase) -> None:
        self._orchestrator = orchestrator
        self._stub = stub
        self._entry = entry
        self._phase = phase
        self.model_id = stub.model_id
        self.phase = phase
        self.budget_us = stub.budget_us
        self.blocks_trade = bool(entry.blocks_trade)
        self.last_summary: dict[str, Any] = {}
        self.last_actual_us = 0.0
        self.last_skew = 0.0
        self.adjusted_signal = 0.0

    def validate_inputs(self, ctx: Any) -> List[str]:
        if getattr(self._entry, "role", None) != "defensive":
            return [f"Defensive stub {self.model_id} has non-defensive catalog role"]
        return []

    def defend(self, ctx: Any, signal: Any) -> FilterDecision:
        self.adjusted_signal = float(signal)
        veto = False
        skew = 0.0
        summary: dict[str, Any] = {}

        from workbench.src.registry.unified_registry import get_model_config

        stub_cfg = get_model_config(self._stub.model_id)
        if stub_cfg.kind == "pdf":
            summary, self.last_actual_us = self._orchestrator._run_pdf_stub(self._stub, ctx, self._phase)
            out = ctx.metadata.get("pdf_composition_outputs", {}).get(self._stub.model_id)
            if out and out.payload is not None:
                payload = out.payload
                if self._entry.blocks_trade and getattr(payload, "cancel_all_quotes", False):
                    veto = True
                if hasattr(payload, "reservation_price_skew"):
                    skew = float(getattr(payload, "reservation_price_skew", 0.0))
                if self._phase == "continuous" and self._stub.model_id == "VPIN_TOXICITY":
                    vp = float(getattr(payload, "VPIN_percentile", 0.0))
                    if vp >= 0.99:
                        self.adjusted_signal *= 0.5
                if hasattr(payload, "toxic_cascade_score"):
                    skew = float(getattr(payload, "reservation_price_skew", 0.0))
        else:
            summary, self.last_actual_us, hyp_sig = self._orchestrator._run_hyp_stub(self._stub, ctx)
            if self._entry.blocks_trade and abs(hyp_sig) > 0.5:
                veto = True

        self.last_summary = dict(summary)
        self.last_skew = skew
        if veto:
            return FilterDecision.veto("DEFENSIVE_VETO", tags=summary)
        if skew:
            return FilterDecision.skew_signal(skew, "DEFENSIVE_SKEW", tags=summary)
        return FilterDecision.passthrough(tags=summary)

    def produce_diagnostics(self, ctx: Any, result: FilterDecision) -> DefensiveDiagnostics:
        warnings: list[str] = []
        if self.last_actual_us > self.budget_us:
            warnings.append("DEFENSIVE_BUDGET_EXCEEDED")
        return DefensiveDiagnostics(
            model_id=self.model_id,
            metrics={
                "actual_us": self.last_actual_us,
                "budget_us": self.budget_us,
                "vetoed": result.vetoed,
                "skew": self.last_skew,
            },
            warnings=warnings,
        )


class CompositionOrchestrator:
    """Run primary alpha model with optional defensive stubs by phase."""

    def __init__(self) -> None:
        self._pdf = PdfOrchestrator()

    def _pdf_kwargs(self, ctx: RunContext) -> dict[str, Any]:
        bars = ctx.metadata.get("pdf_bars", [])
        mid = 4500.0
        if len(ctx.events):
            mid = float(ctx.events[-1]["px"])
        trade_times = [
            float(ev["local_ts"]) / 1e9
            for ev in ctx.events
            if str(ev.get("action", "")).upper() == "TRADE"
        ][-50:]
        return {
            "mid": mid,
            "volume": 100.0,
            "bars": bars,
            "timestamp_ns": int(ctx.events[-1]["local_ts"]) if len(ctx.events) else 0,
            "market_order_times": trade_times,
            "t": trade_times[-1] if trade_times else 1.0,
            "symbol": ctx.metadata.get("symbol", "MES"),
        }

    def _run_pdf_stub(
        self,
        stub: DefensiveStub,
        ctx: RunContext,
        phase: Phase,
    ) -> Tuple[dict[str, Any], float]:
        t0 = time.perf_counter()
        outputs = self._pdf.run_subset([stub.model_id], **self._pdf_kwargs(ctx))
        actual_us = (time.perf_counter() - t0) * 1e6
        out = outputs.get(stub.model_id)
        summary: dict[str, Any] = {}
        if out is not None and out.payload is not None:
            for f in fields(out.payload):
                v = getattr(out.payload, f.name)
                if isinstance(v, (int, float, bool, str)):
                    summary[f.name] = v
        ctx.metadata.setdefault("pdf_composition_outputs", {})[stub.model_id] = out
        return summary, actual_us

    def _run_hyp_stub(
        self,
        stub: DefensiveStub,
        ctx: RunContext,
    ) -> Tuple[dict[str, Any], float, float]:
        t0 = time.perf_counter()
        adapter = get_model_by_id(stub.model_id)
        features = adapter.build_features(ctx)
        signal = adapter.generate_signals(features)
        actual_us = (time.perf_counter() - t0) * 1e6
        return {"hyp_signal": signal}, actual_us, signal

    def _apply_stub(
        self,
        stub: DefensiveStub,
        ctx: RunContext,
        trace: CompositionTrace,
        signal: float,
        phase: Phase,
    ) -> Tuple[float, bool]:
        entry = get_catalog_entry(stub.model_id)
        defensive = _CatalogDefensiveShim(self, stub, entry, phase)
        validation_errors = defensive.validate_inputs(ctx)
        if validation_errors:
            raise ValueError("; ".join(validation_errors))
        decision = defensive.defend(ctx, signal)
        diagnostics = defensive.produce_diagnostics(ctx, decision)
        veto = decision.vetoed
        skew = defensive.last_skew
        summary = dict(defensive.last_summary)
        actual_us = defensive.last_actual_us
        signal = defensive.adjusted_signal

        if phase == "during" and decision.action == FilterAction.SKEW and skew:
            signal = signal + skew

        if diagnostics.metrics or diagnostics.series or diagnostics.warnings:
            summary["diagnostics"] = {
                "metrics": diagnostics.metrics,
                "series": diagnostics.series,
                "warnings": diagnostics.warnings,
            }

        trace.steps.append(
            CompositionTraceStep(
                model_id=stub.model_id,
                phase=phase,
                budget_us=stub.budget_us,
                actual_us=actual_us,
                vetoed_trade=veto,
                skew_applied=skew,
                output_summary=summary,
            )
        )
        if actual_us > stub.budget_us:
            trace.steps[-1].output_summary["budget_exceeded"] = True

        return signal, veto

    def _ensure_pdf_context(self, ctx: RunContext) -> None:
        if ctx.metadata.get("pdf_bars"):
            return
        from features_engine.src.features.mbo_features import OrderBook
        from features_engine.src.features.npz_feed import iter_mbo_events

        book = OrderBook()
        bars: list[dict] = []
        prev_mid = None
        vol_acc = 0
        for mbo in iter_mbo_events(ctx.events):
            book.apply_event(mbo)
            mid = (book.get_best_bid() + book.get_best_ask()) / 2 if book.get_best_bid() > 0 else 0
            if mbo.action == "TRADE":
                vol_acc += mbo.size
            if prev_mid is not None and vol_acc >= 50:
                bars.append({"mid": mid, "volume": float(vol_acc), "timestamp_ns": mbo.timestamp_ns})
                vol_acc = 0
            prev_mid = mid
        ctx.metadata["pdf_bars"] = bars

    def _primary_raw_signal(self, primary: Any, ctx: RunContext, features: Any) -> float:
        model_id = primary.model_id
        if model_id in _PDF_SIGNAL_PRIMARIES:
            self._pdf.run_subset([model_id], **self._pdf_kwargs(ctx))
            out = self._pdf.get_output(model_id)
            if out is not None and out.payload is not None:
                field_name = primary.config.signal_field if getattr(primary, "config", None) else "signal"
                val = getattr(out.payload, field_name, None)
                if val is not None:
                    return float(val)
            return 0.0
        return float(primary.generate_signals(features))

    def run(
        self,
        ctx: RunContext,
        composition: ModelComposition,
    ) -> Tuple[Any, CompositionTrace]:
        validation_errors = validate_composition(composition)
        if validation_errors:
            raise ValueError("; ".join(validation_errors))
        stubs = resolve_stub_dependencies(composition.defensive_stubs)
        if stubs:
            self._ensure_pdf_context(ctx)
        self._pdf.clear()
        trace = CompositionTrace(primary_model_id=composition.primary_model_id)
        trace.phase_budgets_us = {p: 0.0 for p in _PHASE_ORDER}

        for stub in stubs:
            if stub.enabled:
                trace.phase_budgets_us[stub.phase] = trace.phase_budgets_us.get(stub.phase, 0.0) + stub.budget_us

        primary = get_model_by_id(composition.primary_model_id)
        features = primary.build_features(ctx)
        signal = self._primary_raw_signal(primary, ctx, features)
        trace.signal_raw = signal

        vetoed = False
        for phase in _PHASE_ORDER:
            phase_stubs = [s for s in stubs if s.enabled and s.phase == phase]
            for stub in phase_stubs:
                signal, veto = self._apply_stub(stub, ctx, trace, signal, phase)
                entry = get_catalog_entry(stub.model_id)
                if veto and (phase == "before" or entry.blocks_trade):
                    vetoed = True
                    trace.trades_vetoed += 1

        trace.signal_adjusted = 0.0 if vetoed else signal
        ctx.metadata["composition_signal_raw"] = trace.signal_raw
        ctx.metadata["composition_signal"] = trace.signal_adjusted
        ctx.metadata["composition_veto"] = vetoed
        ctx.metadata["composition_trace"] = trace.to_dict()

        if vetoed:
            return (
                BacktestResult(
                    hypothesis_id=0,
                    net_pnl=0.0,
                    num_trades=0,
                    win_rate=0.0,
                    expectancy=0.0,
                    adverse_selection_ticks=0.0,
                    tail_loss=0.0,
                    fills=[],
                ),
                trace,
            )

        result = primary.run_backtest(ctx)
        return result, trace

    @staticmethod
    def default_composition(primary_model_id: str) -> ModelComposition:
        """Empty defensive stack (primary only)."""
        return ModelComposition(primary_model_id=primary_model_id, defensive_stubs=[])
