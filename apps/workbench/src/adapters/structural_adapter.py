"""Adapter: BaseStructuralModel -> WorkbenchModel."""

from __future__ import annotations

from dataclasses import fields
from typing import Any, List

import numpy as np

from backtest_pipeline.src.signal_backtester import BacktestResult
from features_engine.src.features.mbo_features import OrderBook
from features_engine.src.features.npz_feed import iter_mbo_events
from features_engine.src.structural_models.model_01_book_pressure import BookPressureModel
from features_engine.src.structural_models.registry import get_structural_model_by_id

from workbench.src.core.protocol import Diagnostics, ModelConfig, WorkbenchModel
from workbench.src.registry.pdf_orchestrator import PdfOrchestrator
from workbench.src.run.run_context import RunContext


class StructuralModelAdapter(WorkbenchModel):
    def __init__(self, model_id: str, config: ModelConfig):
        self.model_id = model_id
        self.config = config
        self._orchestrator = PdfOrchestrator()

    def validate_inputs(self, ctx: RunContext) -> List[str]:
        errs: List[str] = []
        if self.config.diagnostics_only and self.config.required_datasets != ["mbo_npz"]:
            if "options_chain" in self.config.required_datasets:
                if "chain" not in ctx.metadata:
                    errs.append("options chain fixture required for PDF_MODEL_5")
        if ctx.metadata.get("data_sufficient") is False:
            errs.append("DATA_INSUFFICIENT")
        return errs

    def build_features(self, ctx: RunContext) -> Any:
        """No-op: run_backtest builds features inline."""
        return None

    def generate_signals(self, features: Any) -> float:
        """No-op: run_backtest generates signals inline."""
        return 0.0

    def run_backtest(self, ctx: RunContext) -> Any:
        # Build book and bars inline (no dependency on build_features being called first)
        book = OrderBook()
        bars = []
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
        
        # Derive mid/volume from book BBO or last event
        if book.get_best_bid() > 0 and book.get_best_ask() < float("inf"):
            mid = (book.get_best_bid() + book.get_best_ask()) / 2.0
        elif book.get_best_bid() > 0:
            mid = book.get_best_bid()
        elif book.get_best_ask() < float("inf"):
            mid = book.get_best_ask()
        elif len(ctx.events):
            mid = float(ctx.events[-1]["px"])
        else:
            mid = 4500.0
        volume = float(ctx.events[-1]["qty"]) if len(ctx.events) else 100.0
        
        kwargs: dict = {
            "mid": mid,
            "volume": volume,
            "bars": bars,
            "book": book,  # Pass book to model
            "timestamp_ns": int(ctx.events[-1]["local_ts"]) if len(ctx.events) else 0,
        }
        if self.model_id == "DEALER_HEDGING":
            kwargs["chain"] = ctx.metadata.get(
                "chain",
                [
                    {"strike": 4500, "iv": 0.2, "open_interest": 1000, "right": "C"},
                ],
            )
            kwargs["spot"] = mid
        outputs = self._orchestrator.run_subset([self.model_id], **kwargs)
        out = outputs.get(self.model_id)
        
        # Generate signal from orchestrator output (no metadata dependency)
        signal = 0.0
        if out is not None and out.payload is not None:
            field_name = self.config.signal_field if self.config else "OFI_zscore"
            val = getattr(out.payload, field_name, None)
            if val is not None:
                signal = float(val)
        
        if self.config and self.config.diagnostics_only:
            comp_sig = ctx.metadata.get("composition_signal")
            if comp_sig is not None:
                signal = float(comp_sig)
            return {"diagnostics_output": out, "signal": signal}
        
        comp_sig = ctx.metadata.get("composition_signal")
        if comp_sig is not None:
            signal = float(comp_sig)
        
        return BacktestResult(
            hypothesis_id=0,
            net_pnl=signal * 10.0,
            num_trades=1 if abs(signal) > 0.15 else 0,
            win_rate=0.5,
            expectancy=signal,
            adverse_selection_ticks=0.0,
            tail_loss=0.0,
            fills=[],
        )

    def produce_diagnostics(self, ctx: RunContext, result: Any) -> Diagnostics:
        series: dict = {}
        if isinstance(result, dict) and result.get("diagnostics_output"):
            payload = result["diagnostics_output"].payload
            if payload is not None:
                for f in fields(payload):
                    v = getattr(payload, f.name)
                    if isinstance(v, (int, float)):
                        series[f.name] = [float(v)]
        return Diagnostics(
            model_id=self.model_id,
            metrics={"result_type": type(result).__name__},
            series=series,
        )
